"""MAP-H H3 #4 (maps audit 2026-11-06): ONE overall collect deadline.

Pre-registered acceptance (plan `maps_audit_2026_11_06.md`): "ONE overall
collect deadline (setting, default ~20 s): a source past it records
`collect_timeout` and the collect finishes with the rest." Operator 11-06
14:53: default 45 s — measured fleet-wide, the slowest HEALTHY source
(`meshcore_public` on the manager) peaked at 34.1 s over 24 h.

Before this, sources ran serially with no overall bound: one slow public
fetch held ``_collect_lock`` (and the snapshot's freshness) for as long as it
liked. After H3 #1 no REQUEST waits on the lock, so the deadline bounds
FRESHNESS: the refresher publishes on time and says which source was late.

The hard part is the abandoned worker (hfm #8): a source past the deadline
keeps running and writes per-cycle state through ``self`` — its diagnostic,
the position-less list, the meshtasticd node count. Those writes must never
land in the cycle that gave up on it, nor in the next one.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from utils.map_data_collector import MapDataCollector

SOURCES = [
    "_collect_unified_tracker", "_collect_meshtasticd", "_collect_direct_radio",
    "_collect_mqtt", "_collect_node_tracker", "_collect_aredn",
    "_collect_aredn_worldmap", "_collect_rns_direct", "_collect_meshcore_public",
    "_collect_public_fallbacks",
]
DIAG = {  # method -> diagnostic key it is timed under in _collect_locked
    "_collect_unified_tracker": "unified_tracker", "_collect_meshtasticd": "meshtasticd",
    "_collect_mqtt": "mqtt", "_collect_node_tracker": "node_tracker",
    "_collect_aredn": "aredn", "_collect_aredn_worldmap": "aredn_worldmap",
    "_collect_rns_direct": "rns_direct", "_collect_meshcore_public": "meshcore_public",
    "_collect_public_fallbacks": "public_fallback",
}
DEADLINE = 1.0
FLOOR = 0.5


def _feature(fid, lon=-155.0, lat=19.7):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"id": fid, "name": fid, "network": "meshtastic"}}


class _Source:
    """A planted source: counts calls, records its own diagnostic like a real
    one, optionally blocks on a gate, and can write late state when released."""

    def __init__(self, c, key, features=(), gate=None, late_write=False, delay=0.0):
        self.c, self.key, self.features = c, key, list(features)
        self.gate, self.late_write, self.calls = gate, late_write, 0
        self.delay = delay
        self.done = threading.Event()

    def __call__(self, *a, **kw):
        self.calls += 1
        try:
            if self.delay:
                time.sleep(self.delay)  # real work that fits the floor, not instant
            if self.gate is not None:
                self.gate.wait(10)
                if self.late_write:
                    # What a real source does through self after finishing late.
                    self.c._record_diagnostic(self.key, attempted=7, yielded=7)
                    self.c._nodes_without_position.append({"id": "LATE-ZOMBIE"})
                    self.c._total_nodes_seen = 999
                    # ...and an IN-PLACE edit of an entry the cycle already
                    # published (a shared entry dict would leak it).
                    entry = self.c._source_diagnostics.get("unified_tracker")
                    if entry is not None:
                        entry["notes"] = "LATE-ZOMBIE"
            self.c._record_diagnostic(self.key, attempted=len(self.features),
                                      yielded=len(self.features))
            return list(self.features)
        finally:
            self.done.set()


@pytest.fixture
def c(tmp_path):
    coll = MapDataCollector(cache_dir=tmp_path, config_dir=tmp_path, enable_history=False)
    coll._settings["collect_deadline_seconds"] = DEADLINE
    coll.SOURCE_FLOOR_SECONDS = FLOOR
    coll._meshtasticd_present = lambda: True  # never the direct-radio fallback
    coll.planted = {}
    for name in SOURCES:
        src = _Source(coll, DIAG.get(name, name))
        coll.planted[name] = src
        setattr(coll, name, src)
    gates = []
    coll.gates = gates

    def _no_net(*_a, **_k):
        raise OSError("network blocked in unit test")
    with patch("socket.socket", _no_net):
        yield coll
    for g in gates:
        g.set()
    for src in coll.planted.values():
        if src.gate is not None:
            assert src.done.wait(10), "planted slow source outlived the test"


def _slow(c, name, late_write=False):
    gate = threading.Event()
    c.gates.append(gate)
    src = _Source(c, DIAG[name], gate=gate, late_write=late_write)
    c.planted[name] = src
    setattr(c, name, src)
    return src, gate


def _collect_bounded(c, limit):
    """Run one collect in a thread; return (result, seconds) or fail at limit."""
    out = {}

    def run():
        t0 = time.monotonic()
        try:
            out["r"] = c.collect(max_age_seconds=0)
        except BaseException as e:  # surface it, not as KeyError('r')
            out["e"] = e
        out["s"] = time.monotonic() - t0
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(limit)
    assert not t.is_alive(), f"collect() did not return within {limit}s — no deadline"
    if "e" in out:
        raise out["e"]
    return out["r"], out["s"]


def test_default_deadline_is_45s_and_a_setting(tmp_path):
    coll = MapDataCollector(cache_dir=tmp_path, config_dir=tmp_path, enable_history=False)
    assert MapDataCollector.DEFAULT_COLLECT_DEADLINE_SECONDS == 45
    assert coll._settings.get("collect_deadline_seconds") == 45


def test_a_source_past_the_deadline_is_collect_timeout_and_the_rest_publish(c):
    c.planted["_collect_unified_tracker"].features = [_feature("!fast")]
    _slow(c, "_collect_meshcore_public")
    geo, secs = _collect_bounded(c, DEADLINE + 3)
    assert secs < DEADLINE + 1.5
    d = geo["properties"]["source_diagnostics"]
    assert d["meshcore_public"]["reason_if_zero"] == "collect_timeout"
    assert d["meshcore_public"]["yielded"] == 0
    assert d["meshcore_public"]["latency_ms"] >= int(DEADLINE * 1000) * 0.9
    assert d["unified_tracker"]["reason_if_zero"] == "ok"
    assert "!fast" in {f["properties"]["id"] for f in geo["features"]}


def test_an_abandoned_source_cannot_write_into_this_cycle_or_the_next(c):
    src, gate = _slow(c, "_collect_meshcore_public", late_write=True)
    c.planted["_collect_meshtasticd"].features = []
    _collect_bounded(c, DEADLINE + 3)
    gate.set()                       # the zombie now finishes and writes late
    assert src.done.wait(5)
    d = c.get_source_diagnostics()
    assert d["meshcore_public"]["reason_if_zero"] == "collect_timeout", d["meshcore_public"]
    assert "LATE-ZOMBIE" not in {n.get("id") for n in c.get_nodes_without_position()}
    assert c._total_nodes_seen != 999
    assert d["unified_tracker"].get("notes") != "LATE-ZOMBIE", d["unified_tracker"]
    # The next cycle starts clean too (the zombie wrote nowhere it can reach).
    setattr(c, "_collect_meshcore_public", _Source(c, "meshcore_public"))
    geo, _ = _collect_bounded(c, DEADLINE + 3)
    assert geo["properties"]["source_diagnostics"]["meshcore_public"]["reason_if_zero"] != "collect_timeout"
    assert "LATE-ZOMBIE" not in {n.get("id") for n in c.get_nodes_without_position()}
    assert c._total_nodes_seen != 999


def test_a_wedged_early_source_does_not_blind_the_rest_cycle_after_cycle(c):
    # Fable review 2026-11-07, run: meshtasticd is source #2 with its own 45 s
    # cap == the deadline; wedged, it spent the whole budget and every later
    # source read "not run" EVERY cycle. "The collect finishes with the rest."
    c.planted["_collect_aredn"].features = [_feature("!aredn")]
    _slow(c, "_collect_meshtasticd")
    for src in c.planted.values():
        if src.gate is None:
            src.delay = FLOOR / 5  # each later source needs real time, under the floor
    for cycle in (1, 2):
        geo, secs = _collect_bounded(c, DEADLINE + 10 * FLOOR + 3)
        d = geo["properties"]["source_diagnostics"]
        assert d["meshtasticd"]["reason_if_zero"] == "collect_timeout", (cycle, d["meshtasticd"])
        for name in ["_collect_mqtt", "_collect_node_tracker", "_collect_aredn",
                     "_collect_aredn_worldmap", "_collect_rns_direct",
                     "_collect_meshcore_public", "_collect_public_fallbacks"]:
            assert c.planted[name].calls == cycle, (cycle, name, "did not run")
            assert d[DIAG[name]]["reason_if_zero"] != "collect_timeout", (cycle, name, d[DIAG[name]])
        assert "!aredn" in {f["properties"]["id"] for f in geo["features"]}, cycle


def test_after_the_deadline_each_remaining_source_is_bounded_by_the_floor(c):
    _slow(c, "_collect_unified_tracker")       # spends the whole deadline
    _slow(c, "_collect_meshcore_public")       # then wedges on its floor
    geo, secs = _collect_bounded(c, DEADLINE + 10 * FLOOR + 3)
    assert secs < DEADLINE + FLOOR + 1.0, secs  # one floor spent, not unbounded
    d = geo["properties"]["source_diagnostics"]["meshcore_public"]
    assert d["reason_if_zero"] == "collect_timeout"
    assert "floor" in d["notes"], d
    assert c.planted["_collect_public_fallbacks"].calls == 1  # the one after it still ran


def test_a_late_source_records_no_metric_against_the_published_timeout(c):
    src, gate = _slow(c, "_collect_meshcore_public", late_write=True)
    with patch("utils.map_metrics.record_collect") as rec:
        _collect_bounded(c, DEADLINE + 10 * FLOOR + 3)
        gate.set()
        assert src.done.wait(5)
    late = [call for call in rec.call_args_list
            if call.args[:2] == ("meshcore_public", 7)]
    assert late == [], "the abandoned worker's metric contradicts collect_timeout"


@pytest.mark.parametrize("bad", [0, -5, "abc", None, float("nan"), float("inf"), 1e10, 3601])
def test_a_deadline_setting_nobody_can_mean_falls_back_loudly(c, bad, caplog):
    c._settings["collect_deadline_seconds"] = bad
    with caplog.at_level("WARNING"):
        assert c._collect_deadline_s() == MapDataCollector.DEFAULT_COLLECT_DEADLINE_SECONDS
        c._collect_deadline_s()
    assert sum("collect_deadline_seconds" in r.message for r in caplog.records) == 1


def test_the_deadline_is_disarmed_after_every_collect(c):
    # Armed outside a collect, a bare _timed_collect (tests, TUI helpers)
    # would be bounded by a stale deadline — or skip on a long-spent one.
    _collect_bounded(c, DEADLINE + 3)
    assert c._collect_deadline_at is None

    def broken():
        raise RuntimeError("collect body failed")
    c._collect_locked = broken
    with pytest.raises(RuntimeError):
        c._run_collect_witnessed()
    assert c._collect_deadline_at is None


def test_a_worker_killed_by_a_baseexception_reads_unreachable(c):
    def dies(*a, **kw):
        raise SystemExit("source called sys.exit")
    c._collect_aredn = dies
    geo, _ = _collect_bounded(c, DEADLINE + 3)
    d = geo["properties"]["source_diagnostics"]["aredn"]
    assert d["reason_if_zero"] == "unreachable"
    assert "worker died: SystemExit" in d["notes"], d


def test_a_source_still_running_from_last_cycle_is_not_started_twice(c):
    src, gate = _slow(c, "_collect_meshcore_public")
    _collect_bounded(c, DEADLINE + 3)
    assert src.calls == 1
    _collect_bounded(c, DEADLINE + 3)          # zombie still blocked on the gate
    assert src.calls == 1, "a second copy of a still-running source was started"
    d = c.get_source_diagnostics()["meshcore_public"]
    assert d["reason_if_zero"] == "collect_timeout"
    assert "still running" in (d["notes"] or ""), d


def test_a_healthy_cycle_keeps_every_sources_own_state(c):
    def tracker(*a, **kw):
        c._record_diagnostic("node_tracker", attempted=2, yielded=1, notes="mine")
        c._nodes_without_position.append({"id": "!nopos"})
        return [_feature("!t")]

    def meshtasticd(*a, **kw):
        c._total_nodes_seen = 42
        c._record_diagnostic("meshtasticd", attempted=42, yielded=1)
        return [_feature("!m", -155.2)]
    c._collect_node_tracker, c._collect_meshtasticd = tracker, meshtasticd
    geo, _ = _collect_bounded(c, DEADLINE + 3)
    d = geo["properties"]["source_diagnostics"]
    assert d["node_tracker"] == {**d["node_tracker"], "attempted": 2, "yielded": 1,
                                 "reason_if_zero": "ok", "notes": "mine"}
    assert all("latency_ms" in d[k] for k in ("node_tracker", "meshtasticd", "mqtt"))
    assert "!nopos" in {n.get("id") for n in c.get_nodes_without_position()}
    assert c._total_nodes_seen == 42
    assert {"!t", "!m"} <= {f["properties"]["id"] for f in geo["features"]}


def test_a_raising_source_is_still_isolated_as_unreachable(c):
    def boom(*a, **kw):
        raise ValueError("malformed")
    c._collect_aredn = boom
    geo, _ = _collect_bounded(c, DEADLINE + 3)
    d = geo["properties"]["source_diagnostics"]["aredn"]
    assert d["reason_if_zero"] == "unreachable"
    assert "ValueError" in d["notes"]


def test_a_source_starting_with_less_than_the_floor_left_still_gets_the_floor(c):
    # Fable r2 #3 (mutant `remaining <= 0` survived): the floor applies whenever
    # LESS than a floor is left, not only once the deadline is fully spent.
    first = c.planted["_collect_unified_tracker"]
    first.delay = DEADLINE - 0.6 * FLOOR          # leaves ~0.6 floor of the deadline
    c.planted["_collect_meshtasticd"].delay = 0.8 * FLOOR
    geo, _ = _collect_bounded(c, DEADLINE + 10 * FLOOR + 3)
    d = geo["properties"]["source_diagnostics"]["meshtasticd"]
    assert d["reason_if_zero"] != "collect_timeout", d


def test_the_kicked_refresh_outwaits_the_worst_case_collect(tmp_path):
    # Fable r2 #1: a fixed 60 s lock wait < 45 + floor x sources = a false
    # "a collect may be wedged" on a collect progressing by design.
    coll = MapDataCollector(cache_dir=tmp_path, config_dir=tmp_path, enable_history=False)
    worst = (MapDataCollector.DEFAULT_COLLECT_DEADLINE_SECONDS
             + MapDataCollector.SOURCE_FLOOR_SECONDS * MapDataCollector.MAX_TIMED_SOURCES_PER_COLLECT)
    assert coll._refresh_lock_wait_s() > worst
    coll._settings["collect_deadline_seconds"] = 120     # follows the setting
    assert coll._refresh_lock_wait_s() > 120 + worst - 45


def test_the_worst_case_counts_every_timed_source():
    import inspect
    body = inspect.getsource(MapDataCollector._collect_locked)
    assert body.count("_timed_collect(") <= MapDataCollector.MAX_TIMED_SOURCES_PER_COLLECT


def test_a_huge_deadline_setting_cannot_break_every_collect(c):
    # Fable r3, run: 1e10 / inf passed `> 0`, and t.join / Lock.acquire raised
    # OverflowError — every collect failed, the kicked refresh died unwitnessed.
    c._settings["collect_deadline_seconds"] = float("inf")
    geo, _ = _collect_bounded(c, DEADLINE + 3)
    assert geo["type"] == "FeatureCollection"
    assert c._refresh_lock_wait_s() < 1e6
