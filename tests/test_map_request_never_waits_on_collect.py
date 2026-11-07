"""MAP-H H3 #1 + #2: a request thread NEVER waits on ``_collect_lock``.

Measured 2026-11-06: ``collect()``'s cache-miss path does ``with
self._collect_lock:`` -- every HTTP caller with a cache older than 30 s
BLOCKED behind a collect that ran ~26 s healthy on the manager (meshcore_public
25.7 s) and longer when a source wedges. The periodic refresher already
collects off-request; requests read the last snapshot and SAY its age.

Pinned here (round-1 non-author review findings marked r1#N):
  #1 with the lock held by a planted long collect, geojson + topology answer
     within 1 s -- the last snapshot, else 503 ``warming`` (cold start) or 503
     ``blind`` + the error (collector failing, r1#1);
  #1 a stale snapshot kicks exactly ONE background refresh; a kick is never
     dropped behind a holder that did not refresh (r1#9);
  #2 age header + ``/api/status.snapshot``, monotonic (r1#7), coherent with the
     bytes it labels (generation-keyed cache, r1#3), stale line sane with the
     refresher off (r1#2), published atomically (r1#6);
  witness: every collect driver counts failures (r1#4); the warmer never kicks
     a second collect (r1#5).

SAFETY: the ``collector`` fixture patches ``_collect_locked`` for EVERY test
and joins the kicked refresh thread at teardown, so no background thread can
outlive its patch and run a real collect against this box.
"""
import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from utils.map_data_collector import MapDataCollector, Snapshot
from utils.map_http_handler import MapRequestHandler
from utils.map_data_service import _make_http_server  # the live listener class

FC = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-155.0, 19.7]},
     "properties": {"id": "!a", "name": "A", "network": "meshtastic"}}],
    "properties": {}}
FC2 = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-155.1, 19.8]},
     "properties": {"id": "!b", "name": "B", "network": "meshtastic"}}],
    "properties": {}}


class _Calls:
    """Stands in for _collect_locked: counts, optionally blocks/raises/publishes."""

    def __init__(self, c):
        self.c, self.n, self.gate, self.exc, self.publish = c, 0, None, None, None

    def __call__(self):
        self.n += 1
        if self.gate is not None:
            self.gate.wait(5)
        if self.exc is not None:
            raise self.exc
        if self.publish is not None:
            self.c._publish_snapshot(self.publish)
        return self.c._cached_geojson


@pytest.fixture
def collector(tmp_path, monkeypatch):
    c = MapDataCollector(cache_dir=tmp_path, config_dir=tmp_path, enable_history=False)
    c._settings["collect_cache_max_age_seconds"] = 30
    calls = _Calls(c)
    monkeypatch.setattr(c, "_collect_locked", calls)
    monkeypatch.setattr(c, "_SNAPSHOT_REFRESH_LOCK_WAIT_S", 3.0)
    c.calls = calls
    yield c
    if calls.gate is not None:
        calls.gate.set()
    t = c._snapshot_refresh_thread
    if t is not None:
        t.join(6)
        assert not t.is_alive(), "kicked refresh outlived the test"


def _stale(c, age_s=120.0, geo=FC):
    """A snapshot exists but is past the cache window (published the real way)."""
    c._publish_snapshot(geo, mono=time.monotonic() - age_s)


def _join_refresh(c):
    t = c._snapshot_refresh_thread
    if t is not None:
        t.join(6)


class _HeldLock:
    """A planted long collect: holds _collect_lock until released."""

    def __init__(self, c):
        self.c, self.release_evt, self.held = c, threading.Event(), threading.Event()

    def __enter__(self):
        def run():
            with self.c._collect_lock:
                self.held.set()
                self.release_evt.wait(30)
        self.t = threading.Thread(target=run, daemon=True)
        self.t.start()
        assert self.held.wait(2)
        return self

    def __exit__(self, *exc):
        self.release_evt.set()
        self.t.join(5)


@pytest.fixture
def server(collector, monkeypatch):
    monkeypatch.setattr(MapRequestHandler, "is_warming", False)
    monkeypatch.setattr(MapRequestHandler, "collector", collector)
    # /api/status probes this box's radio; a test never touches it (tx_guard refuses)
    monkeypatch.setattr(MapRequestHandler, "_get_local_radio_config", lambda self: {})
    monkeypatch.setattr(MapRequestHandler, "_get_radio_status_summary", lambda self: {})
    srv = _make_http_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _get(srv, path, timeout=3.0):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=timeout)
    try:
        t0 = time.monotonic()
        c.request("GET", path, headers={"Host": "127.0.0.1"})
        r = c.getresponse()
        body = r.read()
        return r.status, dict(r.getheaders()), body, time.monotonic() - t0
    finally:
        c.close()


def _ids(body):
    return {f["properties"]["id"] for f in json.loads(body)["features"]}


# ── #1 never wait ────────────────────────────────────────────────────────
def test_geojson_serves_the_stale_snapshot_while_a_collect_holds_the_lock(server, collector):
    _stale(collector)
    with _HeldLock(collector):
        status, _h, body, took = _get(server, "/api/nodes/geojson")
    assert status == 200, body[:200]
    assert took < 1.0, f"request waited {took:.2f}s on _collect_lock"
    assert _ids(body) == {"!a"}


def test_geojson_with_no_snapshot_and_a_held_lock_is_warming_not_a_hang(server, collector):
    with _HeldLock(collector):
        status, _h, body, took = _get(server, "/api/nodes/geojson")
    assert took < 1.0, f"request waited {took:.2f}s on _collect_lock"
    assert status == 503, body[:200]
    assert json.loads(body)["state"] == "warming"


def test_topology_never_waits_on_the_collect_lock(server, collector):
    _stale(collector)
    with _HeldLock(collector):
        status, _h, body, took = _get(server, "/api/network/topology")
    assert took < 1.0, f"request waited {took:.2f}s on _collect_lock"
    assert status == 200, body[:200]


@pytest.mark.parametrize("path", ["/api/nodes/geojson", "/api/network/topology"])
def test_a_failing_collector_with_no_snapshot_reads_blind_not_warming(server, collector, path):
    # r1#1: prewarm failed, is_warming already cleared, collects keep failing
    collector.calls.exc = RuntimeError("meshtasticd leg exploded")
    with pytest.raises(RuntimeError):
        collector.collect()  # the prewarm path
    status, _h, body, _t = _get(server, path)
    _join_refresh(collector)
    doc = json.loads(body)
    assert status == 503 and doc["state"] == "blind", doc
    assert "meshtasticd leg exploded" in doc["reason"]


def test_a_stale_snapshot_kicks_exactly_one_background_refresh(collector):
    _stale(collector)
    collector.calls.gate = threading.Event()
    got = [collector.snapshot() for _ in range(25)]
    deadline = time.monotonic() + 2
    while not collector.calls.n and time.monotonic() < deadline:
        time.sleep(0.02)
    collector.calls.gate.set()
    _join_refresh(collector)
    assert all(g is not None and g.geojson is FC for g in got)
    assert collector.calls.n == 1, f"{collector.calls.n} background collects for one stale window"


def test_a_kick_is_not_dropped_behind_a_holder_that_did_not_refresh(collector):
    # r1#9: a TUI collect() holding the lock for its cache re-check must not eat the kick
    _stale(collector)
    collector.calls.publish = FC2
    with _HeldLock(collector):
        collector.snapshot()  # kicked while the lock is held by a non-refresher
    _join_refresh(collector)
    assert collector.calls.n == 1
    assert collector.peek_snapshot().geojson is FC2


def test_a_kick_after_someone_else_refreshed_does_not_collect_again(collector):
    _stale(collector)
    with _HeldLock(collector):
        collector.snapshot()
        collector._publish_snapshot(FC2)  # the holder DID refresh
    _join_refresh(collector)
    assert collector.calls.n == 0


def test_a_fresh_snapshot_kicks_no_refresh(collector):
    collector._publish_snapshot(FC)
    snap = collector.snapshot()
    assert snap.geojson is FC and 0 <= snap.age_s < 5
    assert collector._snapshot_refresh_thread is None and collector.calls.n == 0


def test_collect_for_non_request_callers_still_collects_synchronously(collector):
    # TUI / exporters / prewarm keep the blocking contract: no snapshot -> collect now.
    collector.calls.publish = FC
    assert collector.collect() is FC
    assert collector.calls.n == 1


# ── #2 the age is true ───────────────────────────────────────────────────
def test_snapshot_age_is_monotonic_not_wall_clock(collector):
    _stale(collector, age_s=90.0)
    collector._last_collect = time.time() + 3600  # NTP stepped the wall clock
    assert 85 <= collector.peek_snapshot().age_s <= 100


def test_state_without_a_publish_is_no_snapshot(collector):
    # r1#7: no wall-clock fallback -- only a published snapshot has an age
    collector._cached_geojson = FC
    collector._last_collect = time.time()
    assert collector.peek_snapshot() is None and collector.snapshot_age() is None


def test_the_atomic_snapshot_lands_before_the_legacy_cache(collector):
    # r1#6: whoever sees _cached_geojson (the warming watcher) finds a stamped snapshot
    seen = []

    class Spy(type(collector)):
        def __setattr__(self, name, value):
            if name == "_cached_geojson" and value is not None:
                seen.append(self.__dict__.get("_snapshot_state"))
            object.__setattr__(self, name, value)

    collector.__class__ = Spy
    collector._publish_snapshot(FC)
    assert seen and seen[0] is not None and seen[0][0] is FC


def test_geojson_says_the_snapshot_age(server, collector):
    _stale(collector, age_s=75.0)
    status, headers, _b, _t = _get(server, "/api/nodes/geojson")
    assert status == 200
    assert 70 <= float(headers["X-Snapshot-Age"]) <= 90
    assert float(headers["X-Snapshot-Stale-After"]) == 600


def test_cached_bytes_never_carry_a_newer_snapshots_age(server, collector):
    # r1#3: bytes built from S1 must not be served under S2's age
    _stale(collector, age_s=500.0, geo=FC)
    _s1, h1, b1, _t = _get(server, "/api/nodes/geojson")
    collector._publish_snapshot(FC2)  # a refresh publishes S2 inside the cache TTL
    _s2, h2, b2, _t = _get(server, "/api/nodes/geojson")
    assert _ids(b1) == {"!a"} and float(h1["X-Snapshot-Age"]) >= 490
    assert _ids(b2) == {"!b"} and float(h2["X-Snapshot-Age"]) < 5


def test_a_warmed_slice_is_keyed_to_its_own_snapshot(collector):
    collector._settings["geojson_warm_regions"] = ["hawaii"]
    collector._publish_snapshot(FC)
    gen1 = collector.peek_snapshot().generation
    collector._warm_geojson_regions()
    assert collector._geojson_response_cache.get((gen1, None, "hawaii", None)) is not None
    collector._publish_snapshot(FC2)
    gen2 = collector.peek_snapshot().generation
    assert collector._geojson_response_cache.get((gen2, None, "hawaii", None)) is None


def test_status_says_the_snapshot_age(server, collector):
    _stale(collector, age_s=75.0)
    status, _h, body, _t = _get(server, "/api/status")
    assert status == 200
    snap = json.loads(body)["snapshot"]
    # 75 s is normal between refreshes: STALE means the refresher missed twice
    assert snap["state"] == "ok" and 70 <= snap["age_s"] <= 90, snap
    assert snap["stale_after_s"] == 2 * MapDataCollector.DEFAULT_PERIODIC_REFRESH_SECONDS


def test_status_says_stale_once_the_refresher_missed_twice(server, collector):
    _stale(collector, age_s=2 * MapDataCollector.DEFAULT_PERIODIC_REFRESH_SECONDS + 60)
    _s, _h, body, _t = _get(server, "/api/status")
    assert json.loads(body)["snapshot"]["state"] == "stale"


@pytest.mark.parametrize("interval", [0, -5, "junk"])
def test_a_disabled_refresher_does_not_make_every_snapshot_stale(collector, interval):
    # r1#2: periodic_refresh_seconds <= 0 disables the loop; the line stays real
    collector._settings["periodic_refresh_seconds"] = interval
    _stale(collector, age_s=75.0)
    st = collector.snapshot_status()
    assert st["state"] == "ok" and st["stale_after_s"] == 600, st


def test_status_says_no_snapshot_yet(server, collector):
    status, _h, body, _t = _get(server, "/api/status")
    assert status == 200
    snap = json.loads(body)["snapshot"]
    assert snap["state"] == "none" and snap["age_s"] is None, snap


# ── witness ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("driver", ["kick", "collect", "periodic"])
def test_every_collect_driver_leaves_a_witness(collector, driver):
    # r1#4: request kick, prewarm/TUI collect(), and the periodic tick all count
    collector.calls.exc = RuntimeError("boom")
    if driver == "kick":
        collector.snapshot()
        _join_refresh(collector)
    elif driver == "collect":
        with pytest.raises(RuntimeError):
            collector.collect()
    else:
        collector._periodic_refresh_interval = 0.01
        collector._periodic_refresh_stop.clear()
        t = threading.Thread(target=collector._periodic_refresh_loop, daemon=True)
        t.start()
        deadline = time.monotonic() + 3
        while not collector._collect_failures and time.monotonic() < deadline:
            time.sleep(0.02)
        collector._periodic_refresh_stop.set()
        t.join(3)
    st = collector.snapshot_status()
    assert st["collect_failures"] >= 1 and "boom" in st["last_error"], st


def test_a_good_collect_clears_the_last_error(collector):
    collector.calls.exc = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        collector.collect()
    collector.calls.exc, collector.calls.publish = None, FC
    collector.collect()
    assert collector.snapshot_status()["last_error"] is None


def test_the_warmer_never_kicks_a_second_collect(collector):
    # r1#5: after a FAILED periodic collect the snapshot is stale; warming must not re-collect
    collector._settings["geojson_warm_regions"] = ["hawaii"]
    _stale(collector, age_s=400.0)
    collector._warm_geojson_regions()
    assert collector._snapshot_refresh_thread is None and collector.calls.n == 0


def test_the_warmer_with_no_snapshot_is_quiet(collector, caplog):
    collector._settings["geojson_warm_regions"] = ["hawaii"]
    collector._warm_geojson_regions()
    assert collector._snapshot_refresh_thread is None
    assert not [r for r in caplog.records if r.levelname in ("ERROR", "CRITICAL")]


# ── the page ─────────────────────────────────────────────────────────────
def test_the_page_reads_the_age_and_the_servers_own_stale_line():
    # One threshold, owned by the server (hfm #5): the page compares the two
    # headers it is sent and never hardcodes its own copy of the stale line.
    html = (Path(__file__).resolve().parents[1] / "web" / "node_map.html").read_text()
    assert "response.headers.get('X-Snapshot-Age')" in html
    assert "response.headers.get('X-Snapshot-Stale-After')" in html
    assert "if (snapAge > staleAfter)" in html
    assert "the collector has not refreshed it" in html


def test_the_page_says_blind_when_the_collector_is_failing():
    html = (Path(__file__).resolve().parents[1] / "web" / "node_map.html").read_text()
    assert "body.state === 'blind'" in html
    assert "map collector failing: " in html


def test_snapshot_is_a_named_triple():
    s = Snapshot(FC, 1.5, 3)
    assert (s.geojson, s.age_s, s.generation) == (FC, 1.5, 3)


# ── round 2 (Fable, by running) ──────────────────────────────────────────
def test_response_bytes_of_past_generations_are_not_held(server, collector):
    # r2#1: generation keys must not leak -- each publish evicts the old bytes
    for i in range(40):
        collector._publish_snapshot(FC if i % 2 else FC2, mono=time.monotonic() - 1)
        assert _get(server, "/api/nodes/geojson")[0] == 200
    assert collector._geojson_response_cache.stats()["entry_count"] <= 1


def test_a_wedged_lock_holder_leaves_a_witness(collector, caplog, monkeypatch):
    # r2#2: the kicked refresh timing out on the lock is the wedge signal
    monkeypatch.setattr(collector, "_SNAPSHOT_REFRESH_LOCK_WAIT_S", 0.2)
    _stale(collector, age_s=2 * MapDataCollector.DEFAULT_PERIODIC_REFRESH_SECONDS + 60)
    with caplog.at_level("WARNING"), _HeldLock(collector):
        collector.snapshot()
        _join_refresh(collector)
    st = collector.snapshot_status()
    assert st["refresh_lock_timeouts"] == 1 and collector.calls.n == 0, st
    assert "wedged" in st["stale_reason"]
    assert any("gave up" in r.getMessage() for r in caplog.records)


def test_a_published_snapshot_is_stale_on_both_clocks(collector):
    # r2#3: the legacy wall stamp says the same age, so collect()'s own cache
    # check cannot call a stale snapshot fresh (and tests age it for real)
    _stale(collector, age_s=120.0)
    assert 115 <= time.time() - collector._last_collect <= 125


def test_a_failed_kick_does_not_rewarm_the_old_snapshot(collector, monkeypatch):
    warms = []
    monkeypatch.setattr(collector, "_warm_geojson_regions", lambda: warms.append(1))
    collector.calls.exc = RuntimeError("boom")
    _stale(collector)
    collector.snapshot()
    _join_refresh(collector)
    assert collector.calls.n == 1 and warms == []


# ── round 3 (Fable, by running) ──────────────────────────────────────────
def test_a_collect_driven_publish_rewarms_the_region_slices(collector):
    # r3#1: prewarm/TUI/exporter collect() publishes (clearing the cache) -- it must re-warm
    collector._settings["geojson_warm_regions"] = ["hawaii"]
    collector.calls.publish = FC2
    collector.collect()
    gen = collector.peek_snapshot().generation
    assert collector._geojson_response_cache.get((gen, None, "hawaii", None)) is not None


def test_an_old_cleared_wedge_is_not_blamed_for_a_later_stale_window(collector, monkeypatch):
    # r3#2: stale_reason needs a lock timeout DURING this snapshot's life
    monkeypatch.setattr(collector, "_SNAPSHOT_REFRESH_LOCK_WAIT_S", 0.2)
    _stale(collector, age_s=60.0)
    with _HeldLock(collector):
        collector.snapshot()
        _join_refresh(collector)
    assert collector._refresh_lock_timeouts == 1
    # the wedge cleared: a healthy publish, which then ages past the stale line
    # for an unrelated reason (no requests, refresher off)
    _stale(collector, age_s=2 * MapDataCollector.DEFAULT_PERIODIC_REFRESH_SECONDS + 60)
    collector._last_lock_timeout_mono = time.monotonic() - 3 * MapDataCollector.DEFAULT_PERIODIC_REFRESH_SECONDS
    st = collector.snapshot_status()
    assert st["state"] == "stale" and "stale_reason" not in st, st
