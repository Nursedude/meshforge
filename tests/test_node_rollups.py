"""Tests for ``moc_analysis_tool.aggregators.node_rollups``.

Pattern-audit Finding #3 (2026-05-19): ``NodeRollup.obs_count`` is
documented as "From node_history.node_observations (window-bound)" but
prior to commit at this test's creation, the implementation pulled the
value from the ``nodes`` directory table — where ``obs_count`` is a
per-UPSERT counter that inflates with every external-bulk republish
(meshcore_public, aredn_worldmap, mqtt_global, public_fallback).

Downstream consequence: the ``most_reliable`` leaderboard's
``min_obs >= 50`` qualification threshold could be satisfied by nodes
with a single real observation that had been republished 50× by an
external bulk source, misleading audience-facing rankings.

These tests pin the corrected contract.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from moc_analysis_tool.aggregators.node_rollups import collect_node_rollups
from moc_analysis_tool.spec import AnalysisSpec, TimeWindow
from utils.node_history import NodeHistoryDB


def _spec(tmp_path: Path, *, window_hours: int = 24) -> AnalysisSpec:
    """Build an AnalysisSpec pointing at temp DBs.

    The presentation_capture DB only needs the schema — the rollup builder
    handles an empty packets table gracefully (no packet rollups; directory
    + observations still drive obs_count).
    """
    cap_path = tmp_path / "presentation_capture.db"
    nh_path = tmp_path / "node_history.db"
    # Minimal presentation_capture schema — just enough for the rollup
    # builder's SELECT not to error. Set WAL mode so the rollup builder's
    # connect_tuned(mode=ro) doesn't try to flip journal mode on a fresh
    # read-only connection (which raises "readonly database").
    conn = sqlite3.connect(str(cap_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS packets (
                source       TEXT NOT NULL,
                port_name    TEXT NOT NULL,
                timestamp    REAL NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    return AnalysisSpec(
        name="test",
        region_key=None,
        window=TimeWindow.last_n_hours(window_hours),
        db_path=cap_path,
        node_history_path=nh_path,
    )


def _seed_directory_upserts(hist: NodeHistoryDB, node_id: str, count: int) -> None:
    """Inflate the directory's obs_count for `node_id` via repeated UPSERTs.

    Mirrors the external-bulk republish pattern: every cycle pushes the
    same row through ``record_observations``, ticking ``obs_count`` without
    a real new observation. Bypasses the per-node throttle by backdating
    ``_last_recorded`` between calls (key is created on the first call;
    setdefault handles the initial case).
    """
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [0.1, 0.2]},
        "properties": {
            "id": node_id,
            "name": node_id,
            "is_online": True,
            "network": "meshtastic",
            "source_origin": "meshcore_public",
        },
    }
    for _ in range(count):
        # Pre-set _last_recorded into the past so the throttle lets this
        # observation through. Without this, every call after the first
        # would be silently dropped by record_observations.
        hist._last_recorded[node_id] = 0.0
        hist.record_observations([feature])


def _seed_observations(
    hist: NodeHistoryDB,
    node_id: str,
    count: int,
    *,
    is_online: bool = True,
    base_ts: float | None = None,
) -> None:
    """Write `count` rows directly into node_observations under the window.

    Bypasses the throttle: we want a precise per-test count, not the
    one-per-minute that record_observations enforces. Schema columns
    match the actual node_observations table (no rssi column).
    """
    if base_ts is None:
        base_ts = time.time() - 600  # within the default 24h window
    conn = sqlite3.connect(str(hist.db_path))
    try:
        for i in range(count):
            conn.execute(
                """
                INSERT INTO node_observations
                  (network, node_id, timestamp, latitude, longitude,
                   altitude, snr, battery, is_online,
                   hardware, role, via_mqtt, name)
                VALUES ('meshtastic', ?, ?, 0.2, 0.1, NULL, 4.0, 90,
                        ?, '', '', 0, '')
                """,
                (node_id, base_ts + i, 1 if is_online else 0),
            )
        conn.commit()
    finally:
        conn.close()


class TestObsCountSourcesFromObservations:
    """The dataclass comment says NodeRollup.obs_count is window-bound from
    node_observations. The implementation must match the documentation —
    pattern-audit Finding #3."""

    def test_obs_count_reflects_observations_not_directory_upserts(self, tmp_path):
        """A node with 3 directory UPSERTs but 7 real observations in the
        window must surface obs_count=7."""
        spec = _spec(tmp_path)
        hist = NodeHistoryDB(db_path=tmp_path / "node_history.db")
        # 3 directory upserts (republish-style)
        _seed_directory_upserts(hist, "!aabbccdd", count=3)
        # 7 real observations in the window
        _seed_observations(hist, "!aabbccdd", count=7)

        rollups = collect_node_rollups(spec)
        r = rollups.get("!aabbccdd")
        assert r is not None
        assert r.obs_count == 7, (
            f"obs_count={r.obs_count} — should match real observations (7), "
            "not directory upserts (3)"
        )

    def test_obs_count_zero_for_directory_only_node(self, tmp_path):
        """A node that exists only in the directory (no node_observations
        rows in the window) must surface obs_count=0 — NOT the inflated
        directory upsert counter."""
        spec = _spec(tmp_path)
        hist = NodeHistoryDB(db_path=tmp_path / "node_history.db")
        # Inflate directory: 100 upserts, zero real observations
        _seed_directory_upserts(hist, "!republished", count=100)

        rollups = collect_node_rollups(spec)
        r = rollups.get("!republished")
        assert r is not None, (
            "directory-only node should still appear in rollups (for name/"
            "position fields); only obs_count must be correct"
        )
        assert r.obs_count == 0, (
            f"obs_count={r.obs_count} — directory had 100 upserts but "
            "ZERO real window-bound observations; the value MUST be 0 "
            "(pattern-audit Finding #3)"
        )

    def test_obs_count_window_bounded(self, tmp_path):
        """Observations outside the analysis window must not count."""
        spec = _spec(tmp_path, window_hours=1)
        hist = NodeHistoryDB(db_path=tmp_path / "node_history.db")
        # Directory upsert so the node appears in rollups at all (step 2
        # of collect_node_rollups iterates the nodes table; step 3 joins
        # observations by node_id, skipping nodes missing from step 2).
        _seed_directory_upserts(hist, "!a", count=1)
        # 4 observations recent (in window); 6 observations old (outside)
        _seed_observations(hist, "!a", count=4, base_ts=time.time() - 600)
        _seed_observations(hist, "!a", count=6, base_ts=time.time() - 7200)

        rollups = collect_node_rollups(spec)
        r = rollups.get("!a")
        assert r is not None
        assert r.obs_count == 4, (
            f"obs_count={r.obs_count} — only the 4 in-window observations "
            "should count; the 6 outside the 1h window must be excluded"
        )


class TestLeaderboardThresholdNoLongerInflatable:
    """Pattern-audit Finding #3 consequence: the most_reliable leaderboard's
    ``r.obs_count >= min_obs`` gate must reject nodes whose obs_count was
    only inflated by directory republishes, not real observations."""

    def test_republish_inflated_node_no_longer_qualifies(self, tmp_path):
        """A node with 100 directory upserts but only 5 real observations
        must NOT pass the default min_obs=50 leaderboard threshold."""
        from moc_analysis_tool.leaderboards.most_reliable import build as build_most_reliable

        spec = _spec(tmp_path)
        hist = NodeHistoryDB(db_path=tmp_path / "node_history.db")
        # External-bulk-style inflation: 100 republishes
        _seed_directory_upserts(hist, "!cheater", count=100)
        # But only 5 real observations
        _seed_observations(hist, "!cheater", count=5)

        rollup_set = collect_node_rollups(spec)
        leaderboard = build_most_reliable(rollup_set, spec)

        node_ids_on_board = {row.node_id for row in leaderboard.rows}
        assert "!cheater" not in node_ids_on_board, (
            "!cheater had 100 directory upserts but only 5 real "
            "observations — the default min_obs=50 threshold should "
            "filter it out. If this assertion fails the leaderboard is "
            "back to qualifying on inflated upsert count (Finding #3)."
        )

    def test_genuinely_observed_node_qualifies(self, tmp_path):
        """Sanity check: a node with 60 real observations + reasonable
        uptime SHOULD qualify under the default threshold."""
        from moc_analysis_tool.leaderboards.most_reliable import build as build_most_reliable

        spec = _spec(tmp_path)
        hist = NodeHistoryDB(db_path=tmp_path / "node_history.db")
        _seed_directory_upserts(hist, "!honest", count=1)  # baseline upsert
        _seed_observations(hist, "!honest", count=60, is_online=True)

        rollup_set = collect_node_rollups(spec)
        leaderboard = build_most_reliable(rollup_set, spec)

        node_ids_on_board = {row.node_id for row in leaderboard.rows}
        assert "!honest" in node_ids_on_board, (
            "60 real observations + 100% uptime should always qualify "
            "under the default min_obs=50 threshold"
        )
