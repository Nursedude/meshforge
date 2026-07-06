"""Tests for NodeHistoryDB — focused on T2.2 auto-prune behavior.

Issue #44 follow-up: a 14 GB WAL accumulated over 4 days on a fleet box
because record_observations had no automatic pruning. The hourly auto-prune
ensures the WAL can't grow unbounded between manual cleanup() calls.
"""

import sqlite3
import time
from pathlib import Path

import pytest

from utils.node_history import NodeHistoryDB


@pytest.fixture
def hist(tmp_path: Path) -> NodeHistoryDB:
    db_path = tmp_path / "node_history.db"
    return NodeHistoryDB(db_path=db_path, retention_seconds=86400)


def _feature(node_id: str, ts_offset: float = 0.0,
             lat: float = 0.2, lon: float = 0.1,
             network: str = "meshtastic"):
    """Build a minimal GeoJSON feature for record_observations."""
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "id": node_id, "name": node_id, "is_online": True,
            "network": network,
        },
    }


class TestAutoPrune:
    def test_first_call_does_not_prune_old_rows_within_retention(self, hist):
        # Seed with rows inside retention window — should survive any prune.
        hist.record_observations([_feature("!a"), _feature("!b")])
        # Force the prune cadence by clearing the timer.
        hist._last_prune_ts = 0.0
        hist.record_observations([_feature("!c")])
        # All three nodes should be present (none aged out).
        traj_a = hist.get_trajectory("!a", hours=24)
        traj_c = hist.get_trajectory("!c", hours=24)
        assert len(traj_a) == 1
        assert len(traj_c) == 1

    def test_prune_removes_rows_older_than_retention(self, hist):
        # Insert one row, then forge its timestamp into the past beyond retention.
        hist.record_observations([_feature("!old")])
        old_cutoff = time.time() - hist.retention_seconds - 60
        import sqlite3
        conn = sqlite3.connect(str(hist.db_path))
        try:
            conn.execute(
                "UPDATE node_observations SET timestamp = ? WHERE node_id = ?",
                (old_cutoff, "!old"),
            )
            conn.commit()
        finally:
            conn.close()
        # Force prune cadence; new insert triggers the auto-prune check.
        hist._last_prune_ts = 0.0
        # Bypass MIN_RECORD_INTERVAL throttle for !new.
        hist.record_observations([_feature("!new")])
        traj_old = hist.get_trajectory("!old", hours=72)
        traj_new = hist.get_trajectory("!new", hours=72)
        assert traj_old == [], "old row was not pruned"
        assert len(traj_new) == 1

    def test_prune_skipped_within_cadence_window(self, hist):
        # Set a very recent prune so cadence check skips the actual DELETE.
        hist._last_prune_ts = time.time()
        # Force-insert an aged-out row (older than retention).
        hist.record_observations([_feature("!aged")])
        old_cutoff = time.time() - hist.retention_seconds - 60
        import sqlite3
        conn = sqlite3.connect(str(hist.db_path))
        try:
            conn.execute(
                "UPDATE node_observations SET timestamp = ? WHERE node_id = ?",
                (old_cutoff, "!aged"),
            )
            conn.commit()
        finally:
            conn.close()
        # New insert; prune should NOT fire (cadence not reached).
        # Bump _last_prune_ts to JUST inside the cadence window.
        hist._last_prune_ts = time.time() - 60  # 60s ago, well inside 1h cadence
        hist.record_observations([_feature("!fresh")])
        traj_aged = hist.get_trajectory("!aged", hours=72)
        # The aged row should still be present because cadence blocked the DELETE.
        assert len(traj_aged) == 1, "prune fired despite cadence window"

    def test_prune_disabled_when_interval_zero(self, hist):
        # Operators can opt out by setting interval to 0 (e.g., for tests).
        hist._prune_interval_seconds = 0
        hist._last_prune_ts = 0.0  # cadence would otherwise trigger
        hist.record_observations([_feature("!aged")])
        old_cutoff = time.time() - hist.retention_seconds - 60
        import sqlite3
        conn = sqlite3.connect(str(hist.db_path))
        try:
            conn.execute(
                "UPDATE node_observations SET timestamp = ? WHERE node_id = ?",
                (old_cutoff, "!aged"),
            )
            conn.commit()
        finally:
            conn.close()
        hist.record_observations([_feature("!fresh")])
        traj_aged = hist.get_trajectory("!aged", hours=72)
        assert len(traj_aged) == 1, "prune ran despite interval=0 disable"


class TestConnectionPragmas:
    """Lock in WAL + tuned pragmas — regression guard for the fleet-host
    2026-04-26 wedge where rollback-journal mode caused multi-minute
    fdatasync stalls that blocked /api/nodes/geojson responses."""

    def test_journal_mode_is_wal(self, hist):
        # WAL is persistent on the DB header — first connect converts it.
        conn = hist._connect()
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal", f"expected WAL, got {mode!r}"
        finally:
            conn.close()

    def test_synchronous_is_normal(self, hist):
        conn = hist._connect()
        try:
            # synchronous values: 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA
            sync = conn.execute("PRAGMA synchronous").fetchone()[0]
            assert sync == 1, f"expected synchronous=NORMAL (1), got {sync}"
        finally:
            conn.close()

    def test_journal_size_limit_is_capped(self, hist):
        conn = hist._connect()
        try:
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert limit == 67108864, f"expected 64 MB cap, got {limit}"
        finally:
            conn.close()

    def test_wal_persists_across_connections(self, hist):
        # Once one connection sets WAL, subsequent connections inherit it
        # from the DB header — no re-conversion needed.
        c1 = hist._connect()
        c1.close()
        c2 = hist._connect()
        try:
            mode = c2.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
        finally:
            c2.close()


class TestValueDedup:
    """Heartbeat + value-change dedup. Stationary nodes must not flood the
    DB with identical-position rows after the time-throttle has elapsed.
    Mirrors the meshforge-maps Phase 1 fix (commit b264b60)."""

    def _hist(self, tmp_path: Path, **kwargs):
        return NodeHistoryDB(
            db_path=tmp_path / "value_dedup.db",
            retention_seconds=86400,
            **kwargs,
        )

    def test_skips_when_position_unchanged_within_heartbeat(self, tmp_path):
        h = self._hist(tmp_path, heartbeat_seconds=3600)
        # Bypass MIN_RECORD_INTERVAL by aging the first record's last_recorded.
        assert h.record_observations([_feature("!s")]) == 1
        h._last_recorded["!s"] -= 120  # past time-throttle, inside heartbeat
        assert h.record_observations([_feature("!s")]) == 0

    def test_records_when_position_changes(self, tmp_path):
        h = self._hist(tmp_path, heartbeat_seconds=3600)
        assert h.record_observations([_feature("!m", lat=0.2, lon=0.1)]) == 1
        h._last_recorded["!m"] -= 120
        assert h.record_observations([_feature("!m", lat=0.21, lon=0.1)]) == 1

    def test_records_when_heartbeat_elapses_even_unchanged(self, tmp_path):
        h = self._hist(tmp_path, heartbeat_seconds=60)
        assert h.record_observations([_feature("!s")]) == 1
        # Within heartbeat — skipped.
        h._last_recorded["!s"] -= 30
        assert h.record_observations([_feature("!s")]) == 0
        # Past heartbeat — recorded.
        h._last_recorded["!s"] -= 90
        assert h.record_observations([_feature("!s")]) == 1

    def test_first_observation_always_records(self, tmp_path):
        h = self._hist(tmp_path, heartbeat_seconds=3600)
        assert h.record_observations([_feature("!fresh")]) == 1

    def test_round_trip_at_6_decimals_is_treated_as_unchanged(self, tmp_path):
        h = self._hist(tmp_path, heartbeat_seconds=3600)
        assert h.record_observations([
            _feature("!noise", lat=35.123456, lon=139.0)
        ]) == 1
        h._last_recorded["!noise"] -= 120
        # 1e-7 delta — below 6dp threshold — should NOT trigger.
        assert h.record_observations([
            _feature("!noise", lat=35.1234561, lon=139.0)
        ]) == 0

    def test_batch_path_applies_value_dedup(self, tmp_path):
        h = self._hist(tmp_path, heartbeat_seconds=3600)
        h.record_observations([_feature("!a"), _feature("!b")])
        h._last_recorded["!a"] -= 120
        h._last_recorded["!b"] -= 120
        # Same positions for !a and !b → dedup. !c is fresh → recorded.
        n = h.record_observations([
            _feature("!a"), _feature("!b"),
            _feature("!c", lat=0.5, lon=0.5),
        ])
        assert n == 1

    def test_heartbeat_zero_disables_value_dedup(self, tmp_path):
        h = self._hist(tmp_path, heartbeat_seconds=0)
        h.record_observations([_feature("!s")])
        h._last_recorded["!s"] -= 120
        # Time-throttle satisfied; no value-dedup → write happens.
        assert h.record_observations([_feature("!s")]) == 1

    def test_network_change_triggers_record(self, tmp_path):
        h = self._hist(tmp_path, heartbeat_seconds=3600)
        h.record_observations([_feature("!multi", network="meshtastic")])
        h._last_recorded["!multi"] -= 120
        # Same position, different network → different observation.
        assert h.record_observations([
            _feature("!multi", network="aredn")
        ]) == 1


# ────────────────────────────────────────────────────────────────────────
# Issue #49 — nodes directory table
# ────────────────────────────────────────────────────────────────────────


def _feature_directory(node_id: str, *, network: str = "meshtastic",
                       lat=None, lon=None, name: str = "",
                       role: str = "", hardware: str = "",
                       source_origin: str = "",
                       last_heard=None,
                       protocol_meta=None):
    """Feature builder for directory tests — supports position-less rows."""
    geom: Dict[str, Any]
    if lat is None or lon is None:
        geom = {}
    else:
        geom = {"type": "Point", "coordinates": [lon, lat]}
    props: Dict[str, Any] = {
        "id": node_id,
        "name": name or node_id,
        "network": network,
        "role": role,
        "hardware": hardware,
    }
    if source_origin:
        props["source_origin"] = source_origin
    if last_heard is not None:
        props["last_heard"] = last_heard
    if protocol_meta is not None:
        props["protocol_meta"] = protocol_meta
    return {"type": "Feature", "geometry": geom, "properties": props}


class TestPhase1SkipObservation:
    """Phase 1 SD-survival fix (2026-05-09): federation-receiver and
    external-bulk features SHOULD upsert into the `nodes` directory but
    must NOT generate `node_observations` rows. Cuts insert rate from
    ~75k/hr to a few hundred/hr on a federation-enabled box; eliminates
    the structural cause of the multi-day backlog the multi-batch prune
    was mitigating."""

    @pytest.fixture
    def hist(self, tmp_path: Path):
        from utils.node_history import NodeHistoryDB
        return NodeHistoryDB(db_path=tmp_path / "phase1.db",
                             retention_seconds=86400)

    def _fed_feature(self, node_id, *, federated_from="moc3",
                     source_origin="local_radio"):
        """A feature shape produced by map_data_collector for a federated
        peer entry. Mirrors line ~1147-1165 of map_data_collector.py."""
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.1, 0.2]},
            "properties": {
                "id": node_id, "name": node_id, "network": "meshtastic",
                "is_online": True,
                "source": "federation",
                "source_origin": source_origin,
                "federated": True,
                "federated_from": federated_from,
            },
        }

    def _local_feature(self, node_id):
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.1, 0.2]},
            "properties": {
                "id": node_id, "name": node_id, "network": "meshtastic",
                "is_online": True,
                "source_origin": "local_radio",
            },
        }

    def _external_bulk_feature(self, node_id, origin="meshcore_public"):
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.1, 0.2]},
            "properties": {
                "id": node_id, "name": node_id, "network": "meshcore",
                "is_online": True,
                "source_origin": origin,
            },
        }

    def test_federated_feature_does_not_insert_observation(self, hist):
        # The whole point of Phase 1: federation receivers don't accumulate
        # trajectory data for nodes the peer already owns.
        inserted = hist.record_observations([self._fed_feature("!fed1")])
        assert inserted == 0, "federated feature should not insert obs"
        traj = hist.get_trajectory("!fed1", hours=24)
        assert traj == [], "federated feature wrote a trajectory row"

    def test_federated_feature_still_populates_directory(self, hist):
        # Directory is the long-tail "did we ever hear this node" record;
        # federation receivers must keep it.
        hist.record_observations([self._fed_feature("!fed_dir")])
        features, _position_less = hist.get_directory_snapshot()
        ids = {f["properties"]["id"] for f in features}
        assert "!fed_dir" in ids, "federated feature missed directory upsert"

    def test_external_bulk_feature_does_not_insert_observation(self, hist):
        # meshcore_public is 43k mostly-stationary nodes per cycle. Their
        # trajectory data was the largest single source of SD writes.
        inserted = hist.record_observations([
            self._external_bulk_feature("!mc_pub", origin="meshcore_public"),
        ])
        assert inserted == 0
        traj = hist.get_trajectory("!mc_pub", hours=24)
        assert traj == []

    def test_all_external_bulk_origins_skip_observations(self, hist):
        from utils.node_history import EXTERNAL_BULK_ORIGINS
        for origin in EXTERNAL_BULK_ORIGINS:
            nid = f"!ext_{origin}"
            inserted = hist.record_observations([
                self._external_bulk_feature(nid, origin=origin),
            ])
            assert inserted == 0, (
                f"{origin} should be in the skip list but inserted obs"
            )

    def test_local_feature_still_inserts_observation(self, hist):
        # Regression guard: local sources MUST still write trajectories.
        # This is the whole reason node_observations exists.
        inserted = hist.record_observations([self._local_feature("!loc1")])
        assert inserted == 1, "local_radio feature lost its observation"
        traj = hist.get_trajectory("!loc1", hours=24)
        assert len(traj) == 1

    def test_mixed_batch_inserts_only_locals(self, hist):
        # Realistic record_observations call shape: federation + external
        # bulk + local in one batch (the daemon merges all collectors
        # before passing to record_observations).
        inserted = hist.record_observations([
            self._fed_feature("!f1"),
            self._fed_feature("!f2"),
            self._external_bulk_feature("!e1"),
            self._local_feature("!l1"),
            self._local_feature("!l2"),
        ])
        assert inserted == 2, (
            f"expected 2 local observations from mixed batch, got {inserted}"
        )
        # All five reached the directory (long-tail record).
        features, _position_less = hist.get_directory_snapshot()
        ids = {f["properties"]["id"] for f in features}
        for nid in ("!f1", "!f2", "!e1", "!l1", "!l2"):
            assert nid in ids, f"{nid} missed directory upsert"

    def test_federated_from_alone_triggers_skip(self, hist):
        # Defensive: a feature with `federated_from` set but no `federated`
        # boolean should still skip. The flag pair is set together by the
        # collector but a hand-built feature might not carry both.
        feat = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.1, 0.2]},
            "properties": {
                "id": "!ff_only", "name": "ff", "network": "meshtastic",
                "source_origin": "local_radio",
                "federated_from": "moc3",
                # no `federated: True` key
            },
        }
        assert hist.record_observations([feat]) == 0


class TestNodesDirectory:
    """UPSERT semantics, position-null preservation, sticky source_origin
    promotion, and protocol_meta size cap on the new `nodes` directory."""

    @pytest.fixture
    def hist(self, tmp_path: Path):
        from utils.node_history import NodeHistoryDB
        return NodeHistoryDB(db_path=tmp_path / "dir.db")

    def _read_dir(self, hist, node_id: str, network: str = "meshtastic"):
        import sqlite3
        with sqlite3.connect(str(hist.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM nodes WHERE network=? AND node_id=?",
                (network, node_id),
            ).fetchone()
            return dict(row) if row else None

    def test_upsert_creates_row(self, hist):
        hist.record_observations([
            _feature_directory("!a", lat=1.0, lon=2.0, name="Alpha",
                               source_origin="local_radio"),
        ])
        row = self._read_dir(hist, "!a")
        assert row is not None
        assert row["name"] == "Alpha"
        assert row["source_origin"] == "local_radio"
        assert row["last_lat"] == pytest.approx(1.0)
        assert row["last_lon"] == pytest.approx(2.0)
        assert row["obs_count"] == 1

    def test_upsert_updates_existing_row(self, hist):
        hist.record_observations([
            _feature_directory("!a", lat=1.0, lon=2.0, source_origin="local_radio"),
        ])
        # Bypass observation-stream throttle so the second pass also runs.
        hist._last_recorded["!a"] -= 120
        hist.record_observations([
            _feature_directory("!a", lat=1.5, lon=2.5, source_origin="local_radio"),
        ])
        row = self._read_dir(hist, "!a")
        assert row["last_lat"] == pytest.approx(1.5)
        assert row["last_lon"] == pytest.approx(2.5)
        assert row["obs_count"] == 2

    def test_position_less_creates_directory_row(self, hist):
        # MeshCore advert with no GPS → still a directory row.
        hist.record_observations([
            _feature_directory("meshcore:abcd", network="meshcore",
                               source_origin="meshcore_public"),
        ])
        row = self._read_dir(hist, "meshcore:abcd", network="meshcore")
        assert row is not None
        assert row["last_lat"] is None
        assert row["last_lon"] is None
        assert row["source_origin"] == "meshcore_public"

    def test_position_less_does_not_clobber_known_position(self, hist):
        # First a position fix.
        hist.record_observations([
            _feature_directory("meshcore:abcd", network="meshcore",
                               lat=19.4, lon=-155.3,
                               source_origin="meshcore_public"),
        ])
        # Then a position-less heartbeat with same id.
        hist.record_observations([
            _feature_directory("meshcore:abcd", network="meshcore",
                               source_origin="meshcore_public"),
        ])
        row = self._read_dir(hist, "meshcore:abcd", network="meshcore")
        # Position is preserved (COALESCE in ON CONFLICT path).
        assert row["last_lat"] == pytest.approx(19.4)
        assert row["last_lon"] == pytest.approx(-155.3)

    def test_sticky_promotion_external_to_local(self, hist):
        # First seen via meshcore_public (external bulk, 7d tier, prio 30).
        hist.record_observations([
            _feature_directory("meshcore:abcd", network="meshcore",
                               source_origin="meshcore_public"),
        ])
        # Now local_radio actually heard it — must promote (prio 100).
        hist.record_observations([
            _feature_directory("meshcore:abcd", network="meshcore",
                               source_origin="local_radio"),
        ])
        row = self._read_dir(hist, "meshcore:abcd", network="meshcore")
        assert row["source_origin"] == "local_radio"

    def test_sticky_promotion_no_demotion(self, hist):
        # First seen via local_radio (high priority).
        hist.record_observations([
            _feature_directory("!a", source_origin="local_radio"),
        ])
        # External bulk shouldn't demote it — sticky preserves local_radio.
        hist.record_observations([
            _feature_directory("!a", source_origin="meshcore_public"),
        ])
        row = self._read_dir(hist, "!a")
        assert row["source_origin"] == "local_radio"

    def test_protocol_meta_blob_capped(self, hist):
        from utils.node_history import _PROTOCOL_META_MAX_BYTES
        # 50 KB blob — well above the 4 KB cap; writer should drop it.
        big = {"x": "y" * 50_000}
        hist.record_observations([
            _feature_directory("!a", lat=1.0, lon=2.0,
                               protocol_meta=big),
        ])
        row = self._read_dir(hist, "!a")
        assert row["protocol_meta"] == ""
        # Sanity: a sub-cap blob makes it through.
        small = {"k": "v"}
        hist._last_recorded["!a"] -= 120
        hist.record_observations([
            _feature_directory("!a", lat=1.0, lon=2.0,
                               protocol_meta=small),
        ])
        row = self._read_dir(hist, "!a")
        assert row["protocol_meta"]
        assert len(row["protocol_meta"].encode("utf-8")) <= _PROTOCOL_META_MAX_BYTES


class TestDirectoryUpstreamTimestamp:
    """Issue #50 / F7 — last_seen for external-bulk origins must reflect the
    upstream `last_heard`, not the moment we re-published the bulk dataset.
    Without this, every collect cycle rewrites last_seen to NOW and the 7d
    external retention tier never fires (saw it live as ~60k rows pinned at
    `oldest_last_seen == newest_last_seen` across 4 of 5 fleet boxes)."""

    @pytest.fixture
    def hist(self, tmp_path: Path):
        from utils.node_history import NodeHistoryDB
        return NodeHistoryDB(db_path=tmp_path / "upstream.db")

    def _read_last_seen(self, hist, node_id: str, network: str = "meshcore"):
        import sqlite3
        with sqlite3.connect(str(hist.db_path)) as conn:
            row = conn.execute(
                "SELECT last_seen FROM nodes WHERE network=? AND node_id=?",
                (network, node_id),
            ).fetchone()
            return row[0] if row else None

    def test_external_bulk_uses_upstream_last_heard(self, hist):
        # meshcore_public bulk feature with last_heard 2 hours ago.
        upstream = time.time() - 7200
        hist.record_observations([
            _feature_directory("meshcore:abc", network="meshcore",
                               source_origin="meshcore_public",
                               last_heard=upstream),
        ])
        seen = self._read_last_seen(hist, "meshcore:abc")
        assert seen == pytest.approx(upstream, abs=1.0), (
            "external-bulk row must seed last_seen from upstream timestamp, "
            "not from now()"
        )

    def test_external_bulk_republish_does_not_advance_last_seen(self, hist):
        upstream = time.time() - 3600
        hist.record_observations([
            _feature_directory("meshcore:abc", network="meshcore",
                               source_origin="meshcore_public",
                               last_heard=upstream),
        ])
        first = self._read_last_seen(hist, "meshcore:abc")
        # Cycle later: the external source republishes the same record,
        # same upstream timestamp. last_seen must NOT bump — this is the
        # whole point of the fix; otherwise the tier clock never fires.
        hist.record_observations([
            _feature_directory("meshcore:abc", network="meshcore",
                               source_origin="meshcore_public",
                               last_heard=upstream),
        ])
        second = self._read_last_seen(hist, "meshcore:abc")
        assert second == pytest.approx(first, abs=0.001)

    def test_external_bulk_newer_upstream_advances_last_seen(self, hist):
        old = time.time() - 7200
        new = time.time() - 60
        hist.record_observations([
            _feature_directory("meshcore:abc", network="meshcore",
                               source_origin="meshcore_public",
                               last_heard=old),
        ])
        hist.record_observations([
            _feature_directory("meshcore:abc", network="meshcore",
                               source_origin="meshcore_public",
                               last_heard=new),
        ])
        seen = self._read_last_seen(hist, "meshcore:abc")
        assert seen == pytest.approx(new, abs=1.0)

    def test_max_semantics_never_regresses_last_seen(self, hist):
        """A stale republish (older than the existing row) must not pull
        last_seen backward. MAX(existing, incoming) on conflict guards this."""
        recent = time.time() - 60
        stale = time.time() - 7200
        hist.record_observations([
            _feature_directory("meshcore:abc", network="meshcore",
                               source_origin="meshcore_public",
                               last_heard=recent),
        ])
        hist.record_observations([
            _feature_directory("meshcore:abc", network="meshcore",
                               source_origin="meshcore_public",
                               last_heard=stale),
        ])
        seen = self._read_last_seen(hist, "meshcore:abc")
        assert seen == pytest.approx(recent, abs=1.0)

    def test_external_bulk_zero_last_heard_falls_back_to_now(self, hist):
        # AREDN worldmap rows whose CSV `last_seen` failed to parse arrive
        # with last_heard=0. That's not "node was last heard at the epoch";
        # it's "we don't know" — fall through to now() so the row at least
        # ages out at the standard 7d external horizon from this insert.
        before = time.time()
        hist.record_observations([
            _feature_directory("aredn_x", network="aredn",
                               source_origin="aredn_worldmap",
                               last_heard=0),
        ])
        seen = self._read_last_seen(hist, "aredn_x", network="aredn")
        assert seen is not None
        assert seen >= before - 1.0
        assert seen <= time.time() + 1.0

    def test_external_bulk_future_upstream_clamped_to_now(self, hist):
        # A misbehaving / clock-skewed upstream that reports a far-future
        # timestamp must not poison the prune horizon (row would never
        # age out). _build_directory_row clamps at <= now.
        future = time.time() + 86400
        before = time.time()
        hist.record_observations([
            _feature_directory("meshcore:future", network="meshcore",
                               source_origin="meshcore_public",
                               last_heard=future),
        ])
        seen = self._read_last_seen(hist, "meshcore:future")
        assert seen is not None
        assert seen <= time.time() + 1.0
        assert seen >= before - 1.0

    def test_local_origin_ignores_last_heard(self, hist):
        # Even if a local-source feature happens to carry an upstream
        # timestamp, last_seen should reflect "we observed this now".
        # Local sources aren't subject to the bulk-republish bloat shape.
        old = time.time() - 7200
        before = time.time()
        hist.record_observations([
            _feature_directory("!localnode", network="meshtastic",
                               lat=1.0, lon=2.0,
                               source_origin="local_radio",
                               last_heard=old),
        ])
        seen = self._read_last_seen(hist, "!localnode", network="meshtastic")
        assert seen is not None
        assert seen >= before - 1.0
        assert seen > old + 60  # not the upstream value

    def test_unknown_origin_ignores_last_heard(self, hist):
        # Defense: only origins explicitly tagged external-bulk should
        # opt into upstream stamping. An unknown/missing origin must
        # behave like local (use now).
        old = time.time() - 7200
        before = time.time()
        hist.record_observations([
            _feature_directory("!mystery", network="meshtastic",
                               lat=1.0, lon=2.0,
                               last_heard=old),
        ])
        seen = self._read_last_seen(hist, "!mystery", network="meshtastic")
        assert seen is not None
        assert seen >= before - 1.0

    def test_external_bulk_pruned_after_upstream_ages_past_7d(self, tmp_path):
        """End-to-end: an external-bulk row whose upstream stamp is 8 days
        old must actually be deleted by the 7d external retention prune,
        even if we just "republished" it this cycle. Pre-fix, this row
        would survive forever because last_seen was rewritten to NOW on
        every UPSERT."""
        from utils.node_history import NodeHistoryDB
        h = NodeHistoryDB(
            db_path=tmp_path / "prune.db",
            directory_retention_external=7 * 86400,
            directory_retention_local=30 * 86400,
        )
        upstream_8d_ago = time.time() - 8 * 86400
        h.record_observations([
            _feature_directory("meshcore:stale", network="meshcore",
                               source_origin="meshcore_public",
                               last_heard=upstream_8d_ago),
        ])
        # Force prune (bypass the hourly cadence guard).
        h._last_prune_ts = 0.0
        h._maybe_prune(time.time())
        import sqlite3
        with sqlite3.connect(str(h.db_path)) as conn:
            row = conn.execute(
                "SELECT 1 FROM nodes WHERE node_id=?", ("meshcore:stale",)
            ).fetchone()
        assert row is None, (
            "8d-old upstream row survived the 7d external prune — "
            "F7 fix is not effective"
        )


class TestDirectoryRetention:
    """Tiered prune (30d local / 7d external) + count-cap LRU."""

    def _hist(self, tmp_path: Path, **kwargs):
        from utils.node_history import NodeHistoryDB
        return NodeHistoryDB(
            db_path=tmp_path / "retention.db",
            retention_seconds=86400,
            **kwargs,
        )

    def _seed(self, hist, node_id, *, source_origin, last_seen_offset_s):
        """Insert one directory row directly so we can age its last_seen."""
        import sqlite3
        now = time.time()
        seeded = now + last_seen_offset_s
        with sqlite3.connect(str(hist.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO nodes (
                    network, node_id, first_seen, last_seen,
                    last_lat, last_lon, source_origin
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("meshtastic", node_id, seeded, seeded,
                 1.0, 2.0, source_origin),
            )
            conn.commit()

    def _count(self, hist):
        import sqlite3
        with sqlite3.connect(str(hist.db_path)) as conn:
            return conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

    def _has(self, hist, node_id):
        import sqlite3
        with sqlite3.connect(str(hist.db_path)) as conn:
            row = conn.execute(
                "SELECT 1 FROM nodes WHERE node_id=?", (node_id,)
            ).fetchone()
            return row is not None

    def test_external_bulk_pruned_at_7d(self, tmp_path):
        h = self._hist(tmp_path)
        # External bulk: 8 days old (past 7d external retention).
        self._seed(h, "!ext_old", source_origin="meshcore_public",
                   last_seen_offset_s=-(8 * 86400))
        # Local: 8 days old (well inside 30d local retention).
        self._seed(h, "!loc_recent", source_origin="local_radio",
                   last_seen_offset_s=-(8 * 86400))
        # Force prune.
        h._last_prune_ts = 0.0
        h._maybe_prune(time.time())
        assert not self._has(h, "!ext_old"), "external row past 7d not pruned"
        assert self._has(h, "!loc_recent"), "local row well inside 30d wrongly pruned"

    def test_local_pruned_at_30d(self, tmp_path):
        h = self._hist(tmp_path)
        # Local: 31 days old (past 30d local retention).
        self._seed(h, "!loc_old", source_origin="local_radio",
                   last_seen_offset_s=-(31 * 86400))
        # External: 6 days old (inside 7d external retention).
        self._seed(h, "!ext_recent", source_origin="meshcore_public",
                   last_seen_offset_s=-(6 * 86400))
        h._last_prune_ts = 0.0
        h._maybe_prune(time.time())
        assert not self._has(h, "!loc_old"), "local row past 30d not pruned"
        assert self._has(h, "!ext_recent"), "external row inside 7d wrongly pruned"

    def test_count_cap_lru_evicts_oldest(self, tmp_path):
        # Cap at 3 — seed 5 rows with staggered last_seen, expect the
        # 2 oldest are evicted.
        h = self._hist(tmp_path, directory_max_rows=3)
        for i in range(5):
            # Offset by -i hours so node_0 is newest, node_4 is oldest.
            self._seed(h, f"!n{i}", source_origin="local_radio",
                       last_seen_offset_s=-(i * 3600))
        assert self._count(h) == 5
        h._last_prune_ts = 0.0
        h._maybe_prune(time.time())
        assert self._count(h) == 3
        # The 3 newest (n0, n1, n2) survive; n3 + n4 evicted.
        for surviving in ("!n0", "!n1", "!n2"):
            assert self._has(h, surviving)
        for evicted in ("!n3", "!n4"):
            assert not self._has(h, evicted)

    def test_observation_retention_cut_to_48h(self, tmp_path):
        from utils.node_history import DEFAULT_RETENTION_SECONDS
        # The default for observation-stream retention dropped from 7d
        # (604800s) to 48h (172800s) when the directory took over the
        # long-tail "did we hear this node" question.
        assert DEFAULT_RETENTION_SECONDS == 48 * 3600

    def test_prune_batch_cap_caps_each_transaction(self, tmp_path):
        """Defensive bound — caught live on moc3 (790MB DB on Pi 3B,
        first prune after 7d→48h cutover generated 465MB WAL and stalled
        the service 10+ minutes). The per-batch cap bounds individual
        transaction size; the per-cycle cap (max_batches) bounds total
        work. With max_batches=1 we get the legacy single-batch
        behavior — backlog rolls over to the next hourly prune."""
        from utils.node_history import NodeHistoryDB
        h = NodeHistoryDB(
            db_path=tmp_path / "batch.db",
            retention_seconds=3600,            # 1h
            prune_batch_limit=5,               # tiny cap for the test
            prune_max_batches_per_cycle=1,     # legacy single-batch
        )
        # Seed 12 aged-out observations directly so we can prove the cap.
        import sqlite3, time as _t
        old = _t.time() - 7200
        with sqlite3.connect(str(h.db_path)) as conn:
            for i in range(12):
                conn.execute(
                    "INSERT INTO node_observations "
                    "(node_id, timestamp, latitude, longitude, network) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"!n{i}", old, 1.0, 2.0, "meshtastic"),
                )
            conn.commit()
        h._last_prune_ts = 0.0
        h._maybe_prune(_t.time())
        # First prune deleted exactly 5 (the cap).
        with sqlite3.connect(str(h.db_path)) as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM node_observations"
            ).fetchone()[0]
        assert remaining == 7, f"expected 7 rows after capped prune, got {remaining}"
        # Second prune cycle picks up the next batch of 5.
        h._last_prune_ts = 0.0
        h._maybe_prune(_t.time())
        with sqlite3.connect(str(h.db_path)) as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM node_observations"
            ).fetchone()[0]
        assert remaining == 2, (
            f"expected 2 rows after second capped prune, got {remaining}"
        )

    def test_prune_multi_batch_drains_backlog_in_one_cycle(self, tmp_path):
        """Multi-batch loop fix (2026-05-09). The default cycle runs up
        to prune_max_batches_per_cycle (12) batches. A backlog of 12
        rows with batch_limit=5 fits in 3 batches (5+5+2) and should
        clear in one cycle — pre-fix this needed three cycles."""
        from utils.node_history import NodeHistoryDB
        h = NodeHistoryDB(
            db_path=tmp_path / "drain.db",
            retention_seconds=3600,
            prune_batch_limit=5,
            prune_max_batches_per_cycle=12,    # default
        )
        import sqlite3, time as _t
        old = _t.time() - 7200
        with sqlite3.connect(str(h.db_path)) as conn:
            for i in range(12):
                conn.execute(
                    "INSERT INTO node_observations "
                    "(node_id, timestamp, latitude, longitude, network) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"!n{i}", old, 1.0, 2.0, "meshtastic"),
                )
            conn.commit()
        h._last_prune_ts = 0.0
        h._maybe_prune(_t.time())
        with sqlite3.connect(str(h.db_path)) as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM node_observations"
            ).fetchone()[0]
        assert remaining == 0, (
            f"multi-batch loop should drain 12 in one cycle, got {remaining} left"
        )

    def test_prune_multi_batch_loop_terminates_early_on_partial_batch(self, tmp_path):
        """The loop breaks as soon as a batch returns fewer rows than
        batch_limit (i.e., backlog drained). Without the early-exit
        guard the loop would issue empty DELETEs for the remaining
        max_batches iterations every cycle, costing nothing but worth
        locking in."""
        from utils.node_history import NodeHistoryDB
        h = NodeHistoryDB(
            db_path=tmp_path / "early.db",
            retention_seconds=3600,
            prune_batch_limit=10,
            prune_max_batches_per_cycle=12,
        )
        import sqlite3, time as _t
        old = _t.time() - 7200
        with sqlite3.connect(str(h.db_path)) as conn:
            # 7 < batch_limit → first batch deletes all 7, rc<10 → loop exits.
            for i in range(7):
                conn.execute(
                    "INSERT INTO node_observations "
                    "(node_id, timestamp, latitude, longitude, network) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"!n{i}", old, 1.0, 2.0, "meshtastic"),
                )
            conn.commit()
        h._last_prune_ts = 0.0
        h._maybe_prune(_t.time())
        with sqlite3.connect(str(h.db_path)) as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM node_observations"
            ).fetchone()[0]
        assert remaining == 0, (
            f"first-batch partial drain should clear all 7, got {remaining}"
        )

    def test_prune_max_batches_per_cycle_caps_total_work(self, tmp_path):
        """Per-cycle cap bounds total work. With batch=5 and
        max_batches=2, a backlog of 30 should leave 20 after one cycle
        (5+5 deleted, 20 remain) — proving the loop exits at the cycle
        cap before draining."""
        from utils.node_history import NodeHistoryDB
        h = NodeHistoryDB(
            db_path=tmp_path / "cyclecap.db",
            retention_seconds=3600,
            prune_batch_limit=5,
            prune_max_batches_per_cycle=2,
        )
        import sqlite3, time as _t
        old = _t.time() - 7200
        with sqlite3.connect(str(h.db_path)) as conn:
            for i in range(30):
                conn.execute(
                    "INSERT INTO node_observations "
                    "(node_id, timestamp, latitude, longitude, network) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"!n{i}", old, 1.0, 2.0, "meshtastic"),
                )
            conn.commit()
        h._last_prune_ts = 0.0
        h._maybe_prune(_t.time())
        with sqlite3.connect(str(h.db_path)) as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM node_observations"
            ).fetchone()[0]
        assert remaining == 20, (
            f"max_batches=2 × batch=5 = 10 deleted; got {30 - remaining} deleted, "
            f"{remaining} remain"
        )

    def test_prune_directory_multi_batch_drains_backlog(self, tmp_path):
        """Phase 2 (directory tier prune) gets the same multi-batch
        treatment as Phase 1. A federation-enabled fleet box
        (2026-05-09) carried 28k+ aged meshcore_public rows that the
        single-batch prune couldn't catch up on. Same shape verified
        for the directory side."""
        h = self._hist(tmp_path, prune_batch_limit=5,
                       prune_max_batches_per_cycle=12)
        # Seed 14 aged external rows (>7d old).
        for i in range(14):
            self._seed(h, f"!ext{i}", source_origin="meshcore_public",
                       last_seen_offset_s=-(8 * 86400))
        h._last_prune_ts = 0.0
        h._maybe_prune(time.time())
        # 14 fits in 3 batches (5+5+4) — all should clear.
        for i in range(14):
            assert not self._has(h, f"!ext{i}"), (
                f"!ext{i} not pruned — directory multi-batch loop regression"
            )

    def test_prune_runs_wal_checkpoint_truncate(self, tmp_path):
        """End-of-cycle PRAGMA wal_checkpoint(TRUNCATE) (2026-05-09 fix).
        Long-running readers (federation polls, /api/nodes/directory
        responses) blocked the default passive checkpoint from ever
        truncating WAL on a federation-enabled fleet box → 688 MB WAL
        accumulated across days. The forced truncate runs every cycle
        and falls through gracefully if blocked.

        Test shape: seed enough aged rows to balloon the WAL across
        multiple batched commits, run prune, then verify the WAL file
        is at or near 0 bytes. With no active reader, TRUNCATE always
        succeeds — which is what we're locking in here."""
        from utils.node_history import NodeHistoryDB
        h = NodeHistoryDB(
            db_path=tmp_path / "wal.db",
            retention_seconds=3600,
            prune_batch_limit=100,
            prune_max_batches_per_cycle=12,
        )
        import sqlite3, time as _t
        old = _t.time() - 7200
        with sqlite3.connect(str(h.db_path)) as conn:
            for i in range(500):
                conn.execute(
                    "INSERT INTO node_observations "
                    "(node_id, timestamp, latitude, longitude, network) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"!n{i}", old, 1.0, 2.0, "meshtastic"),
                )
            conn.commit()
        h._last_prune_ts = 0.0
        h._maybe_prune(_t.time())
        with sqlite3.connect(str(h.db_path)) as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM node_observations"
            ).fetchone()[0]
        assert remaining == 0, (
            f"prune cycle should drain backlog, got {remaining} left"
        )
        # With no other readers, TRUNCATE checkpoints back to 0 bytes
        # (or removes the file entirely on some SQLite builds).
        wal = tmp_path / "wal.db-wal"
        if wal.exists():
            assert wal.stat().st_size == 0, (
                f"WAL not truncated after prune cycle: {wal.stat().st_size} bytes"
            )

    def test_prune_batch_cap_zero_means_unbounded(self, tmp_path):
        # Operators can opt out (legacy behavior): one giant DELETE.
        from utils.node_history import NodeHistoryDB
        h = NodeHistoryDB(
            db_path=tmp_path / "unbounded.db",
            retention_seconds=3600,
            prune_batch_limit=0,
        )
        import sqlite3, time as _t
        old = _t.time() - 7200
        with sqlite3.connect(str(h.db_path)) as conn:
            for i in range(50):
                conn.execute(
                    "INSERT INTO node_observations "
                    "(node_id, timestamp, latitude, longitude, network) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"!n{i}", old, 1.0, 2.0, "meshtastic"),
                )
            conn.commit()
        h._last_prune_ts = 0.0
        h._maybe_prune(_t.time())
        with sqlite3.connect(str(h.db_path)) as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM node_observations"
            ).fetchone()[0]
        assert remaining == 0, f"unbounded prune should clear all 50; left {remaining}"

    def test_public_fallback_is_external_bulk(self, tmp_path):
        # public_fallback (meshmap.net / rmap.world global Meshtastic
        # firehose) IS external bulk and must use the 7d tier — caught
        # live on moc1 first restart where 10,241 public_fallback rows
        # were initially routed to the 30d local tier.
        from utils.node_history import EXTERNAL_BULK_ORIGINS
        assert "public_fallback" in EXTERNAL_BULK_ORIGINS
        h = self._hist(tmp_path)
        # Aged 8 days — past 7d external retention.
        self._seed(h, "!pf_old", source_origin="public_fallback",
                   last_seen_offset_s=-(8 * 86400))
        h._last_prune_ts = 0.0
        h._maybe_prune(time.time())
        assert not self._has(h, "!pf_old"), (
            "public_fallback row past 7d not pruned — tier regression"
        )


class TestDirectoryStats:
    """get_directory_stats() shape — drives the new /api/status block."""

    @pytest.fixture
    def hist(self, tmp_path: Path):
        from utils.node_history import NodeHistoryDB
        return NodeHistoryDB(db_path=tmp_path / "stats.db")

    def test_empty_directory(self, hist):
        s = hist.get_directory_stats()
        assert s["total"] == 0
        assert s["by_network"] == {}
        assert s["with_position"] == 0
        assert s["without_position"] == 0

    def test_aggregate_shape(self, hist):
        hist.record_observations([
            _feature_directory("!a", lat=1.0, lon=2.0,
                               network="meshtastic",
                               source_origin="local_radio"),
            _feature_directory("meshcore:b", network="meshcore",
                               source_origin="meshcore_public"),
            _feature_directory("aredn:c", lat=3.0, lon=4.0,
                               network="aredn",
                               source_origin="aredn_local"),
        ])
        s = hist.get_directory_stats()
        assert s["total"] == 3
        assert s["with_position"] == 2
        assert s["without_position"] == 1
        assert s["by_network"]["meshtastic"] == 1
        assert s["by_network"]["meshcore"] == 1
        assert s["by_network"]["aredn"] == 1
        assert s["by_source_origin"]["local_radio"] == 1
        assert s["by_source_origin"]["meshcore_public"] == 1
        assert s["by_source_origin"]["aredn_local"] == 1
        assert s["retention_local_days"] == 30
        assert s["retention_external_days"] == 7
        assert s["max_rows"] == 15_000


class TestStatsCache:
    """get_stats() TTL cache — Issue #52, prevents 42s lock holds.

    The COUNT(*)/COUNT(DISTINCT) full scans on a few-million-row
    observation table dominate the lock for tens of seconds and stall
    every other API caller. Stats are observability-only; a 60s cache
    is the surgical fix.
    """

    @pytest.fixture
    def hist(self, tmp_path: Path):
        from utils.node_history import NodeHistoryDB
        return NodeHistoryDB(db_path=tmp_path / "stats_cache.db")

    def test_first_call_populates_cache(self, hist):
        s = hist.get_stats()
        assert s["total_observations"] == 0
        assert hist._stats_cache is s
        assert hist._stats_cache_expires > 0

    def test_second_call_returns_cached_object(self, hist):
        first = hist.get_stats()
        second = hist.get_stats()
        # Same dict object — proves it didn't recompute.
        assert first is second

    def test_cache_expires(self, hist):
        first = hist.get_stats()
        # Force cache expiry by rewinding the deadline.
        hist._stats_cache_expires = 0.0
        second = hist.get_stats()
        # Different dict object — proves recompute fired.
        assert first is not second

    def test_cache_respects_ttl_default_60s(self, hist):
        hist.get_stats()
        # Default TTL is 60s; cache should be valid for at least 30s
        # ahead of now (well within the 60s window).
        assert hist._stats_cache_expires > time.time() + 30

    def test_cache_isolated_per_instance(self, tmp_path: Path):
        from utils.node_history import NodeHistoryDB
        h1 = NodeHistoryDB(db_path=tmp_path / "a.db")
        h2 = NodeHistoryDB(db_path=tmp_path / "b.db")
        h1.get_stats()
        # h2 has no cache yet; populating h1 doesn't leak.
        assert h2._stats_cache is None


class TestDirectoryStatsCache:
    """get_directory_stats() TTL cache — 2026-05-13 follow-up to the
    warming-gate fix. /api/status fans five sequential full scans on
    the `nodes` table into one user-facing request; under SD contention
    that sum exceeded HTTP timeout. Cache is observability-only; same
    surgical pattern as TestStatsCache.
    """

    @pytest.fixture
    def hist(self, tmp_path: Path):
        from utils.node_history import NodeHistoryDB
        return NodeHistoryDB(db_path=tmp_path / "dir_stats_cache.db")

    def test_first_call_populates_cache(self, hist):
        s = hist.get_directory_stats()
        assert s["total"] == 0
        assert hist._directory_stats_cache is s
        assert hist._directory_stats_cache_expires > 0

    def test_second_call_returns_cached_object(self, hist):
        first = hist.get_directory_stats()
        second = hist.get_directory_stats()
        # Same dict object — proves it didn't recompute.
        assert first is second

    def test_cache_expires(self, hist):
        first = hist.get_directory_stats()
        hist._directory_stats_cache_expires = 0.0
        second = hist.get_directory_stats()
        assert first is not second

    def test_cache_isolated_per_instance(self, tmp_path: Path):
        from utils.node_history import NodeHistoryDB
        h1 = NodeHistoryDB(db_path=tmp_path / "a.db")
        h2 = NodeHistoryDB(db_path=tmp_path / "b.db")
        h1.get_directory_stats()
        assert h2._directory_stats_cache is None


class TestDirectorySizeBudgetAlarmIssue64:
    """The size-budget alarm answers the reliability backlog #5 question:
    'how do we know when we've hit too late?' on /api/nodes/directory
    growth. record_directory_serialized_size() is called by the HTTP
    serializer; get_directory_stats() surfaces the most recent size +
    a boolean alarm when it crosses the threshold."""

    @pytest.fixture
    def hist(self, tmp_path: Path):
        from utils.node_history import NodeHistoryDB
        return NodeHistoryDB(db_path=tmp_path / "size.db")

    def test_size_fields_default_to_none_before_first_serialize(self, hist):
        """Fresh boxes that haven't served /api/nodes/directory yet show
        nulls rather than fake zeros — operator can tell "we haven't
        measured" apart from "we measured 0 bytes" (impossible)."""
        s = hist.get_directory_stats()
        assert s["size_bytes_raw"] is None
        assert s["size_bytes_compressed"] is None
        assert s["size_compression_ratio"] is None
        assert s["size_last_serialized_ts"] is None
        assert s["size_alarm"] is False

    def test_record_then_stats_reflects_size(self, hist):
        hist.record_directory_serialized_size(
            raw_bytes=5_000_000,        # 5 MB raw
            compressed_bytes=500_000,   # 500 KB on the wire
        )
        s = hist.get_directory_stats()
        assert s["size_bytes_raw"] == 5_000_000
        assert s["size_bytes_compressed"] == 500_000
        assert s["size_compression_ratio"] == 10.0
        assert s["size_last_serialized_ts"] is not None
        assert s["size_alarm"] is False, (
            "5 MB is well under the 40 MB alarm — no alarm expected"
        )

    def test_size_alarm_triggers_above_threshold(self, hist):
        """At today's moc directory size (35 MB), no alarm. At 41 MB,
        alarm triggers — the threshold is 40 MB."""
        from utils.node_history import DEFAULT_DIRECTORY_SIZE_ALARM_BYTES

        hist.record_directory_serialized_size(
            raw_bytes=DEFAULT_DIRECTORY_SIZE_ALARM_BYTES + 1,
            compressed_bytes=4_000_000,
        )
        s = hist.get_directory_stats()
        assert s["size_alarm"] is True
        assert s["size_alarm_threshold_bytes"] == DEFAULT_DIRECTORY_SIZE_ALARM_BYTES

    def test_alarm_does_not_trigger_just_below_threshold(self, hist):
        from utils.node_history import DEFAULT_DIRECTORY_SIZE_ALARM_BYTES

        hist.record_directory_serialized_size(
            raw_bytes=DEFAULT_DIRECTORY_SIZE_ALARM_BYTES - 1,
            compressed_bytes=4_000_000,
        )
        s = hist.get_directory_stats()
        assert s["size_alarm"] is False

    def test_record_invalidates_stats_cache(self, hist):
        """A new size measurement must invalidate the cached stats so
        operators see the FRESH bytes, not a stale 5-min-old snapshot.
        Without this, the alarm would lag the actual size by up to 5
        minutes — useless when the operator is watching a runaway."""
        first = hist.get_directory_stats()
        assert first["size_bytes_raw"] is None
        # The cache is now populated (first is hist._directory_stats_cache).
        assert hist._directory_stats_cache is first

        hist.record_directory_serialized_size(raw_bytes=10_000_000,
                                              compressed_bytes=1_000_000)

        # Cache must have been invalidated.
        assert hist._directory_stats_cache is None
        second = hist.get_directory_stats()
        assert second is not first
        assert second["size_bytes_raw"] == 10_000_000

    def test_compressed_none_when_not_gzipped(self, hist):
        """If the client didn't accept gzip (or the response was below
        the gzip threshold), compressed bytes is None, ratio is None."""
        hist.record_directory_serialized_size(
            raw_bytes=8_000,
            compressed_bytes=None,
        )
        s = hist.get_directory_stats()
        assert s["size_bytes_raw"] == 8_000
        assert s["size_bytes_compressed"] is None
        assert s["size_compression_ratio"] is None


class TestDirectorySnapshot:
    """get_directory_snapshot() returns features + position-less list."""

    @pytest.fixture
    def hist(self, tmp_path: Path):
        from utils.node_history import NodeHistoryDB
        return NodeHistoryDB(db_path=tmp_path / "snap.db")

    def test_split_into_features_and_position_less(self, hist):
        hist.record_observations([
            _feature_directory("!with_pos", lat=1.0, lon=2.0,
                               source_origin="local_radio"),
            _feature_directory("meshcore:no_pos", network="meshcore",
                               source_origin="meshcore_public"),
        ])
        features, position_less = hist.get_directory_snapshot()
        assert len(features) == 1
        assert features[0]["properties"]["id"] == "!with_pos"
        assert features[0]["geometry"]["coordinates"][:2] == [
            pytest.approx(2.0), pytest.approx(1.0)
        ]
        assert features[0]["properties"]["source_origin"] == "local_radio"
        assert "last_seen_age_s" in features[0]["properties"]
        assert "obs_count" in features[0]["properties"]
        assert len(position_less) == 1
        assert position_less[0]["id"] == "meshcore:no_pos"

    def test_include_position_less_false_filters(self, hist):
        hist.record_observations([
            _feature_directory("meshcore:no_pos", network="meshcore",
                               source_origin="meshcore_public"),
        ])
        features, position_less = hist.get_directory_snapshot(
            include_position_less=False
        )
        assert features == []
        assert position_less == []  # explicitly opted out


class TestOriginPriority:
    def test_local_radio_outranks_external_bulk(self):
        from utils.node_history import _origin_priority
        assert _origin_priority("local_radio") > _origin_priority("meshcore_public")
        assert _origin_priority("rns_path_table") > _origin_priority("aredn_worldmap")
        assert _origin_priority("aredn_local") > _origin_priority("mqtt_global")

    def test_unknown_origin_is_low_but_nonzero(self):
        from utils.node_history import _origin_priority
        assert _origin_priority("totally_unknown") == 10
        assert _origin_priority("") == 0
        assert _origin_priority(None) == 0


# ------------------------------------------------------------------
# Node count optimization §D — DB hygiene
# (lower LRU cap, _meta table, weekly gated VACUUM)
# ------------------------------------------------------------------

class TestLoweredLRUCap:
    """The hard count cap on `nodes` dropped 50_000 → 15_000.

    With the §A bbox filter shedding external-bulk volume and §B
    federation off-persistence, the realistic upper bound on a
    regional Pi-class box is ~3k total. The new default gives ~5×
    headroom; bigger fleets can override the ctor arg.
    """

    def test_default_cap_is_15000(self):
        from utils.node_history import DEFAULT_DIRECTORY_MAX_ROWS
        assert DEFAULT_DIRECTORY_MAX_ROWS == 15_000

    def test_db_uses_new_default(self, tmp_path):
        h = NodeHistoryDB(db_path=tmp_path / "n.db", retention_seconds=86400)
        assert h.directory_max_rows == 15_000

    def test_ctor_arg_still_overrides(self, tmp_path):
        h = NodeHistoryDB(db_path=tmp_path / "n.db",
                          retention_seconds=86400,
                          directory_max_rows=50_000)
        assert h.directory_max_rows == 50_000


class TestMetaTable:
    """The tiny key/value `_meta` table holds persisted maintenance state
    (currently only last_vacuum_ts). MF013-compliant — extension inside
    the existing node_history.db, not a new DBSpec."""

    def test_init_creates_meta_table(self, hist):
        # Direct SQLite probe — table must exist after _init_db.
        conn = hist._connect()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='_meta'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None

    def test_get_default_when_missing(self, hist):
        assert hist._meta_get_float("not_a_key", default=42.0) == 42.0

    def test_set_then_get_roundtrip(self, hist):
        hist._meta_set_float("last_vacuum_ts", 1234567.89)
        assert hist._meta_get_float("last_vacuum_ts") == pytest.approx(1234567.89)

    def test_set_overwrites_existing(self, hist):
        hist._meta_set_float("k", 1.0)
        hist._meta_set_float("k", 2.0)
        assert hist._meta_get_float("k") == 2.0

    def test_persists_across_instances(self, tmp_path):
        db_path = tmp_path / "n.db"
        a = NodeHistoryDB(db_path=db_path, retention_seconds=86400)
        a._meta_set_float("last_vacuum_ts", 1000.0)
        b = NodeHistoryDB(db_path=db_path, retention_seconds=86400)
        assert b._meta_get_float("last_vacuum_ts") == 1000.0

    def test_corrupt_value_returns_default(self, hist):
        # Stuff a non-float into the table to simulate a corrupt row.
        conn = hist._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)",
                ("bad", "not a number"),
            )
            conn.commit()
        finally:
            conn.close()
        assert hist._meta_get_float("bad", default=99.0) == 99.0


class TestWeeklyGatedVacuumIssue_D:
    """Phase 4 of _maybe_prune runs VACUUM under TWO gates:
       1. DB file size ≥ DEFAULT_VACUUM_DB_SIZE_THRESHOLD_BYTES (200 MB)
       2. time since last_vacuum_ts ≥ DEFAULT_VACUUM_INTERVAL_SECONDS (7 d)

    The hourly path skips VACUUM otherwise — Pi SD rewrite is multi-
    minute. Weekly is acceptable; never is how 1.95 GB DBs go
    invisible until they wedge fleet boxes (2026-04-26).
    """

    def _force_db_size_ge_threshold(self, hist, monkeypatch):
        """Stub Path.stat so size gate fires without producing a 200 MB DB."""
        from pathlib import Path as _P
        original = _P.stat

        def fake_stat(self, *args, **kwargs):
            if self == hist.db_path:
                class S:
                    st_size = 300 * 1024 * 1024
                return S()
            return original(self, *args, **kwargs)

        monkeypatch.setattr(_P, "stat", fake_stat)

    def test_vacuum_skipped_when_db_small(self, hist, monkeypatch):
        # Fresh tmp DB is well under 200 MB.
        hist.record_observations([_feature("!a")])
        # Cadence reached → _maybe_prune body runs, including Phase 4.
        hist._last_prune_ts = 0.0
        before = hist._meta_get_float("last_vacuum_ts", 0.0)
        hist.record_observations([_feature("!b")])
        after = hist._meta_get_float("last_vacuum_ts", 0.0)
        assert before == 0.0
        assert after == 0.0   # gate did not fire — VACUUM didn't run

    def test_vacuum_skipped_when_recent(self, hist, monkeypatch):
        # Force the size gate, but pin last_vacuum_ts to "just now".
        self._force_db_size_ge_threshold(hist, monkeypatch)
        now = time.time()
        hist._meta_set_float("last_vacuum_ts", now - 60)  # ran 60 s ago
        hist._last_prune_ts = 0.0  # force cadence
        hist.record_observations([_feature("!a")])
        assert hist._meta_get_float("last_vacuum_ts") == pytest.approx(now - 60, rel=1e-3)

    def test_vacuum_runs_when_both_gates_pass(self, hist, monkeypatch):
        self._force_db_size_ge_threshold(hist, monkeypatch)
        # last_vacuum_ts unset (default 0.0) → "ages" past the 7d interval
        # whatever `now` is.
        assert hist._meta_get_float("last_vacuum_ts", 0.0) == 0.0
        hist._last_prune_ts = 0.0
        hist.record_observations([_feature("!a")])
        # last_vacuum_ts must now be set to roughly the current time.
        new = hist._meta_get_float("last_vacuum_ts")
        assert new > 0.0
        assert abs(time.time() - new) < 5.0

    def test_vacuum_interval_constant(self):
        from utils.node_history import (
            DEFAULT_VACUUM_INTERVAL_SECONDS,
            DEFAULT_VACUUM_DB_SIZE_THRESHOLD_BYTES,
        )
        assert DEFAULT_VACUUM_INTERVAL_SECONDS == 7 * 24 * 3600
        assert DEFAULT_VACUUM_DB_SIZE_THRESHOLD_BYTES == 200 * 1024 * 1024


class TestRssiCapture:
    """node_observations RSSI column — source for the Traffic Heartbeat RF panel.

    record_observations historically dropped props['rssi'] even though the
    feature carried it (that is where the working SNR comes from).
    """

    def test_rssi_recorded_from_feature(self, tmp_path):
        h = NodeHistoryDB(db_path=tmp_path / "node_history.db",
                          retention_seconds=86400)
        feat = _feature("!rf")
        feat["properties"]["snr"] = 5.5
        feat["properties"]["rssi"] = -108
        assert h.record_observations([feat]) == 1
        conn = sqlite3.connect(str(tmp_path / "node_history.db"))
        row = conn.execute(
            "SELECT snr, rssi FROM node_observations WHERE node_id=?",
            ("!rf",)).fetchone()
        conn.close()
        assert row == (5.5, -108)

    def test_rssi_null_when_feature_lacks_it(self, tmp_path):
        h = NodeHistoryDB(db_path=tmp_path / "node_history.db",
                          retention_seconds=86400)
        assert h.record_observations([_feature("!no_rf")]) == 1
        conn = sqlite3.connect(str(tmp_path / "node_history.db"))
        row = conn.execute(
            "SELECT rssi FROM node_observations WHERE node_id=?",
            ("!no_rf",)).fetchone()
        conn.close()
        assert row[0] is None

    def test_migration_adds_rssi_column_to_legacy_db(self, tmp_path):
        # A pre-rssi DB: opening NodeHistoryDB must ALTER in the column, then
        # capture rssi on the next observation.
        p = tmp_path / "node_history.db"
        conn = sqlite3.connect(str(p))
        conn.execute(
            "CREATE TABLE node_observations (id INTEGER PRIMARY KEY "
            "AUTOINCREMENT, node_id TEXT NOT NULL, timestamp REAL NOT NULL, "
            "latitude REAL NOT NULL, longitude REAL NOT NULL, altitude REAL, "
            "snr REAL, battery INTEGER, is_online INTEGER DEFAULT 1, "
            "network TEXT DEFAULT 'meshtastic', hardware TEXT DEFAULT '', "
            "role TEXT DEFAULT '', via_mqtt INTEGER DEFAULT 0, "
            "name TEXT DEFAULT '')")
        conn.commit()
        conn.close()
        h = NodeHistoryDB(db_path=p, retention_seconds=86400)  # _init_db migrates
        conn = sqlite3.connect(str(p))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(node_observations)")}
        conn.close()
        assert "rssi" in cols
        feat = _feature("!mig")
        feat["properties"]["rssi"] = -99
        assert h.record_observations([feat]) == 1


class TestBadCoordinateIsolationIssueQA20260705:
    """QA maps audit 2026-07-05: a single malformed coordinate (string/None
    from a source cache) used to raise round(lat,6) and abort the ENTIRE
    record_observations batch — every node's write lost that cycle, swallowed
    at DEBUG. Now the bad feature is skipped, the rest persist."""

    def _feat(self, nid, lon, lat):
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"id": nid, "name": nid, "is_online": True,
                           "network": "meshtastic"},
        }

    def test_string_coord_does_not_abort_batch(self, hist):
        feats = [
            self._feat("!good1", 0.1, 0.2),
            self._feat("!bad", "not-a-number", "19.4"),  # would crash round()
            self._feat("!good2", 0.3, 0.4),
        ]
        n = hist.record_observations(feats)  # must NOT raise
        assert n == 2  # both good rows written, bad one skipped
        assert hist.get_trajectory("!good1", hours=24)
        assert hist.get_trajectory("!good2", hours=24)
        assert hist.get_trajectory("!bad", hours=24) == [] or \
            not hist.get_trajectory("!bad", hours=24)

    def test_numeric_string_coords_are_coerced(self, hist):
        # A source handing floats-as-strings should still record (coerced).
        n = hist.record_observations([self._feat("!s", "0.11", "0.22")])
        assert n == 1


class TestCanonicalMeshtasticIdIssueQA20260705:
    """QA maps audit 2026-07-05: the CLI fallback keyed nodes by the numeric
    `num` verbatim (decimal string), breaking dedup vs the same node's !hex id
    from other sources (fleet-wide numeric-key class)."""

    def test_decimal_string_never_passes_through(self):
        from utils._map_collector_meshtastic import MeshtasticDataCollectorMixin as M
        # !hex string wins
        assert M._canonical_meshtastic_id("!499602d2", 1234567890) == "!499602d2"
        # numeric num formats to hex, decimal string does NOT pass through
        assert M._canonical_meshtastic_id(None, 1234567890) == "!499602d2"
        assert M._canonical_meshtastic_id("1234567890", 1234567890) == "!499602d2"
        # no usable id
        assert M._canonical_meshtastic_id(None, 0) == "unknown"
        assert M._canonical_meshtastic_id("", None) == "unknown"
