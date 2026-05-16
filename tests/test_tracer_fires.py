"""Tests for the /fleet/tracer-fires drilldown data layer.

Covers parsing tracer-<unix>.json fires, peer filtering, time-window
filter (with the cheap filename-stem pre-filter), result truncation,
query-string param validation, and the no-tracer-dir empty path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import tracer_fires
from utils.tracer_fires import (
    _parse_fire_file,
    get_recent_fires,
    parse_query,
)


NOW = 1778900000.0


def _write_fire(dir_: Path, fire_unix: float, self_short: str,
                results):
    """Helper: write a single tracer-<unix>.json file matching the
    schema_version=1 format the tracer emits."""
    path = dir_ / f"tracer-{int(fire_unix)}.json"
    body = {
        "schema_version": 1,
        "fire_at_unix": fire_unix,
        "fire_at_iso": "2026-05-15T17:00:00Z",
        "self_short": self_short,
        "results": results,
    }
    path.write_text(json.dumps(body))
    return path


# ─── _parse_fire_file ───────────────────────────────────────────────────


def test_parse_fire_file_returns_parsed_dict(tmp_path):
    p = _write_fire(tmp_path, NOW, "moc", [
        {"seq": 1, "peer": "moc1", "result": "ok", "rtt_ms": 100},
    ])
    data = _parse_fire_file(p)
    assert data is not None
    assert data["self_short"] == "moc"
    assert data["fire_at_unix"] == NOW


def test_parse_fire_file_returns_none_on_invalid_json(tmp_path):
    p = tmp_path / "tracer-123.json"
    p.write_text("not json")
    assert _parse_fire_file(p) is None


def test_parse_fire_file_returns_none_on_missing_results(tmp_path):
    p = tmp_path / "tracer-123.json"
    p.write_text(json.dumps({"fire_at_unix": NOW}))  # no results
    assert _parse_fire_file(p) is None


def test_parse_fire_file_returns_none_on_missing_fire_at_unix(tmp_path):
    p = tmp_path / "tracer-123.json"
    p.write_text(json.dumps({"results": []}))
    assert _parse_fire_file(p) is None


def test_parse_fire_file_returns_none_on_non_dict_top(tmp_path):
    """A future tracer schema that emits a top-level array shouldn't
    crash the drilldown — parser returns None and caller skips."""
    p = tmp_path / "tracer-123.json"
    p.write_text(json.dumps([{"results": []}]))
    assert _parse_fire_file(p) is None


# ─── get_recent_fires: peer + window filtering ──────────────────────────


def test_get_recent_fires_filters_by_peer(tmp_path, monkeypatch):
    monkeypatch.setattr(tracer_fires, "_tracer_dir", lambda: tmp_path)
    _write_fire(tmp_path, NOW - 100, "moc", [
        {"seq": 1, "peer": "moc1", "result": "ok", "rtt_ms": 100},
        {"seq": 2, "peer": "moc2", "result": "ok", "rtt_ms": 120},
    ])
    payload = get_recent_fires(peer="moc1", since_unix=NOW - 3600)
    assert len(payload["fires"]) == 1
    assert payload["fires"][0]["seq"] == 1
    assert payload["fires"][0]["rtt_ms"] == 100


def test_get_recent_fires_filters_by_window(tmp_path, monkeypatch):
    """Fires before `since_unix` must not appear, even if their
    filename-stem is in range due to clock skew."""
    monkeypatch.setattr(tracer_fires, "_tracer_dir", lambda: tmp_path)
    _write_fire(tmp_path, NOW - 7200, "moc", [
        {"seq": 1, "peer": "moc1", "result": "ok", "rtt_ms": 100},
    ])
    _write_fire(tmp_path, NOW - 300, "moc", [
        {"seq": 1, "peer": "moc1", "result": "ok", "rtt_ms": 110},
    ])
    payload = get_recent_fires(peer="moc1", since_unix=NOW - 3600)
    assert len(payload["fires"]) == 1
    assert payload["fires"][0]["rtt_ms"] == 110


def test_get_recent_fires_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(tracer_fires, "_tracer_dir", lambda: tmp_path)
    _write_fire(tmp_path, NOW - 600, "moc", [
        {"seq": 1, "peer": "moc1", "result": "ok", "rtt_ms": 100},
    ])
    _write_fire(tmp_path, NOW - 60, "moc", [
        {"seq": 1, "peer": "moc1", "result": "ok", "rtt_ms": 110},
    ])
    _write_fire(tmp_path, NOW - 300, "moc", [
        {"seq": 1, "peer": "moc1", "result": "ok", "rtt_ms": 105},
    ])
    payload = get_recent_fires(peer="moc1", since_unix=NOW - 3600)
    assert [f["rtt_ms"] for f in payload["fires"]] == [110, 105, 100]


def test_get_recent_fires_truncates_to_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(tracer_fires, "_tracer_dir", lambda: tmp_path)
    for i in range(10):
        _write_fire(tmp_path, NOW - i * 60, "moc", [
            {"seq": 1, "peer": "moc1", "result": "ok", "rtt_ms": 100 + i},
        ])
    payload = get_recent_fires(peer="moc1", since_unix=NOW - 3600, limit=3)
    assert len(payload["fires"]) == 3
    assert payload["truncated"] is True


def test_get_recent_fires_returns_empty_when_no_tracer_dir(monkeypatch):
    monkeypatch.setattr(tracer_fires, "_tracer_dir", lambda: None)
    payload = get_recent_fires(peer="moc1", since_unix=NOW - 3600)
    assert payload["fires"] == []
    assert payload["reason"] == "no_tracer_dir"


def test_get_recent_fires_skips_corrupt_files(tmp_path, monkeypatch):
    """One bad file shouldn't drop the rest — drilldown is best-effort."""
    monkeypatch.setattr(tracer_fires, "_tracer_dir", lambda: tmp_path)
    _write_fire(tmp_path, NOW - 60, "moc", [
        {"seq": 1, "peer": "moc1", "result": "ok", "rtt_ms": 100},
    ])
    (tmp_path / "tracer-1234567890.json").write_text("corrupt")
    payload = get_recent_fires(peer="moc1", since_unix=NOW - 3600)
    assert len(payload["fires"]) == 1


def test_get_recent_fires_ignores_non_tracer_files(tmp_path, monkeypatch):
    """Stray files in the dir (READMEs, logs) shouldn't be touched."""
    monkeypatch.setattr(tracer_fires, "_tracer_dir", lambda: tmp_path)
    _write_fire(tmp_path, NOW - 60, "moc", [
        {"seq": 1, "peer": "moc1", "result": "ok", "rtt_ms": 100},
    ])
    (tmp_path / "README.md").write_text("hi")
    (tmp_path / "tracer-bogus").write_text("{}")  # missing .json
    payload = get_recent_fires(peer="moc1", since_unix=NOW - 3600)
    assert len(payload["fires"]) == 1


def test_get_recent_fires_payload_includes_metadata(tmp_path, monkeypatch):
    """Self-describing payload: caller can verify host + peer + window
    matched what they asked for (defense against accidentally hitting
    the wrong peer's endpoint)."""
    monkeypatch.setattr(tracer_fires, "_tracer_dir", lambda: tmp_path)
    _write_fire(tmp_path, NOW - 60, "moc", [
        {"seq": 1, "peer": "moc1", "result": "ok", "rtt_ms": 100},
    ])
    payload = get_recent_fires(peer="moc1", since_unix=NOW - 3600)
    assert payload["peer"] == "moc1"
    assert payload["since_unix"] == NOW - 3600
    assert payload["fires_total_seen"] == 1


# ─── parse_query: input validation ──────────────────────────────────────


def test_parse_query_requires_peer():
    peer, since, limit, err = parse_query({})
    assert err == "peer is required"
    assert peer is None


def test_parse_query_accepts_friendly_names():
    """Tracer peer names use friendly short hostnames; allowlist is
    ascii alnum + dash/underscore/dot."""
    for name in ("HostA", "host-b", "host.local", "node_42"):
        peer, since, limit, err = parse_query({"peer": [name]})
        assert err is None, f"{name} should be accepted"
        assert peer == name


def test_parse_query_rejects_traversal_chars():
    """The peer name flows to a comparison only (no filesystem ops),
    but rejecting funky characters early is defense-in-depth — keeps
    log noise readable too."""
    for bad in ("../etc", "a/b", "a;b", "a$b", "a b"):
        peer, since, limit, err = parse_query({"peer": [bad]})
        assert err is not None, f"{bad!r} should be rejected"


def test_parse_query_clamps_since_to_24h_floor():
    """Old since values would force a full directory walk for no
    useful drilldown — clamp to 24h."""
    peer, since, limit, err = parse_query({
        "peer": ["moc1"], "since": ["1000000000"],
    })
    assert err is None
    import time
    assert since > time.time() - 24 * 3600 - 1


def test_parse_query_clamps_limit():
    peer, since, limit, err = parse_query({
        "peer": ["moc1"], "limit": ["9999"],
    })
    assert err is None
    assert limit == 200
    peer, since, limit, err = parse_query({
        "peer": ["moc1"], "limit": ["0"],
    })
    assert err is None
    assert limit == 1


def test_parse_query_rejects_non_numeric_since():
    peer, since, limit, err = parse_query({
        "peer": ["moc1"], "since": ["notanumber"],
    })
    assert err is not None


def test_parse_query_rejects_non_integer_limit():
    peer, since, limit, err = parse_query({
        "peer": ["moc1"], "limit": ["abc"],
    })
    assert err is not None
