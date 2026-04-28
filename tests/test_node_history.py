"""Tests for NodeHistoryDB — focused on T2.2 auto-prune behavior.

Issue #44 follow-up: a 14 GB WAL accumulated over 4 days on a fleet box
because record_observations had no automatic pruning. The hourly auto-prune
ensures the WAL can't grow unbounded between manual cleanup() calls.
"""

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
    if protocol_meta is not None:
        props["protocol_meta"] = protocol_meta
    return {"type": "Feature", "geometry": geom, "properties": props}


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

    def test_prune_batch_cap_caps_observation_delete(self, tmp_path):
        """Defensive bound — caught live on moc3 (790MB DB on Pi 3B,
        first prune after 7d→48h cutover generated 465MB WAL and stalled
        the service 10+ minutes). With prune_batch_limit=10000, the
        excess rolls over to the next hourly prune so the box stays
        responsive during a one-time rebalance."""
        from utils.node_history import NodeHistoryDB
        h = NodeHistoryDB(
            db_path=tmp_path / "batch.db",
            retention_seconds=3600,            # 1h
            prune_batch_limit=5,               # tiny cap for the test
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
        assert s["max_rows"] == 50_000


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
