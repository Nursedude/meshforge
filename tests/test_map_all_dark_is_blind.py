"""MAP-H H12 — every source dark is BLIND, never ``ok`` with 0 nodes.

``_timed_collect`` isolates each source (a raise → ``[]`` + ``unreachable``),
so a cycle where EVERY attempted source failed used to publish a snapshot with
``collect_failures`` 0, ``last_error`` None and ``/api/status.snapshot.state``
``ok`` — the per-source reasons said dark, the verdict said healthy (r2#4).

Acceptance (written before code, 2026-11-20):
  1. Verdict: among the sources ATTEMPTED THIS CYCLE that are not ``inactive``
     by design, if none is lit (state ``ok``, or yielded > 0 and not ``stale``)
     the cycle is blind, with a reason naming each dark source. Nothing
     attempted / all inactive → not blind (a different question). A source NOT
     run this cycle never votes — diagnostics carry last cycle's values.
  2. Witness: a blind cycle counts in ``blind_cycles`` (its own counter --
     ``collect_failures`` stays "collects that raised", review F5) and sets
     ``last_error``; the next lit cycle clears it.
  Review r1 (2026-11-20): derived / replay / never-recording sources do not
  vote (``NON_WITNESS_SOURCES``, MEASURED on the manager: unified_tracker
  derived, node_tracker + public_fallback record no reason); a deadline-
  SKIPPED source still votes; a blind cycle neither re-saves the cache nor
  records history.
  3. ``/api/status.snapshot.state`` == ``blind`` (+ ``blind_reason``); the
     geojson response carries ``X-Map-Blind``; the page shows the banner.
"""
import itertools
import re
from pathlib import Path

import pytest

from utils.map_source_reasons import (
    NON_WITNESS_SOURCES, SOURCE_REASON_STATE, all_dark_reason, reason_state)

# the server + collector fixtures and the HTTP helper live with the H3 tests
from tests.test_map_request_never_waits_on_collect import (  # noqa: F401
    FC, _get, collector, server)

HTML = (Path(__file__).resolve().parent.parent / "web" / "node_map.html").read_text()


def _d(reason, yielded=0):
    return {"reason_if_zero": reason, "yielded": yielded, "attempted": 1}


# ── 1. the verdict, over the whole state table ──────────────────────────────

REASONS = sorted(SOURCE_REASON_STATE) + [None, "never_heard_of_it"]


def _lit(reason, yielded):
    state = reason_state(reason)
    return state == "ok" or (yielded > 0 and state != "stale")


@pytest.mark.parametrize("ra,ya,rb,yb", [
    (ra, ya, rb, yb)
    for ra, rb in itertools.product(REASONS, REASONS)
    for ya, yb in itertools.product((0, 5), (0, 5))])
def test_two_source_verdict_matches_the_rule(ra, ya, rb, yb):
    diags = {"a": _d(ra, ya), "b": _d(rb, yb)}
    voters = [(r, y) for r, y in ((ra, ya), (rb, yb))
              if reason_state(r) != "inactive"]
    expect_blind = bool(voters) and not any(_lit(r, y) for r, y in voters)
    got = all_dark_reason(diags, {"a", "b"})
    assert (got is not None) == expect_blind, (ra, ya, rb, yb, got)


# Hand-written rows (review F7): the table above re-derives the rule, so it can
# only prove the code matches ITSELF. These pin the rule's meaning by hand.
@pytest.mark.parametrize("reason,yielded,lit", [
    ("ok", 0, True),              # answered, empty -> still an answer
    ("no_data", 0, True),
    ("no_positions", 0, True),
    ("unreachable", 0, False),
    ("collect_timeout", 0, False),
    ("collect_timeout", 7, True),  # partial yield before the deadline = data
    ("stale_cached", 0, False),
    ("stale_cached", 30, False),   # last-known data is not a live answer
    (None, 0, False),              # no reason, no yield: no witness
    (None, 3, True),               # unknown reason but it DID yield
    ("never_heard_of_it", 0, False),
])
def test_one_voter_by_hand(reason, yielded, lit):
    got = all_dark_reason({"a": _d(reason, yielded)}, {"a"})
    assert (got is None) == lit, (reason, yielded, got)


def test_non_witness_sources_never_rescue_a_dark_cycle():
    diags = {"meshtasticd": _d("unreachable"),
             "unified_tracker": _d("no_data", 236),
             "node_tracker": _d("ok", 50), "public_fallback": {}}
    got = all_dark_reason(diags, set(diags))
    assert got is not None and "meshtasticd unreachable" in got
    for src in NON_WITNESS_SOURCES:
        assert src not in got  # never blamed either


def test_only_non_witnesses_attempted_is_not_blind():
    assert all_dark_reason({"node_tracker": {}}, {"node_tracker"}) is None


def test_every_real_source_is_a_voter_or_a_named_non_witness():
    # closed enum (hfm #7): a NEW source in _collect_locked must be judged
    import inspect
    from utils.map_data_collector import MapDataCollector
    src = inspect.getsource(MapDataCollector._collect_locked)
    names = set(re.findall(r'_timed_collect\(\s*"(\w+)"', src))
    assert {"meshtasticd", "mqtt", "unified_tracker"} <= names, names
    voters = names - set(NON_WITNESS_SOURCES)
    KNOWN_VOTERS = {"meshtasticd", "direct_radio", "mqtt", "aredn",
                    "aredn_worldmap", "rns_direct", "meshcore_public"}
    assert voters == KNOWN_VOTERS, (
        f"source set changed: {sorted(voters ^ KNOWN_VOTERS)} -- decide: voter "
        "(add to KNOWN_VOTERS) or non-witness (NON_WITNESS_SOURCES + why)")
    for why in NON_WITNESS_SOURCES.values():
        assert why.strip()


def test_reason_names_every_dark_source():
    got = all_dark_reason({"meshtasticd": _d("unreachable"),
                           "mqtt": _d("collect_timeout"),
                           "aredn": _d("not_configured")},
                          {"meshtasticd", "mqtt", "aredn"})
    assert got.startswith("every map source dark")
    assert "meshtasticd unreachable" in got and "mqtt collect_timeout" in got
    assert "aredn" not in got  # inactive by design is not dark


def test_one_lit_source_is_not_blind_even_when_empty():
    assert all_dark_reason({"a": _d("unreachable"), "b": _d("no_data")},
                           {"a", "b"}) is None


def test_nothing_attempted_is_not_blind():
    assert all_dark_reason({"a": _d("unreachable")}, set()) is None
    assert all_dark_reason({"a": _d("source_disabled")}, {"a"}) is None


def test_a_source_not_run_this_cycle_never_votes():
    # "b" was lit LAST cycle; it did not run this one — it cannot rescue it
    diags = {"a": _d("unreachable"), "b": _d("ok", 40)}
    assert all_dark_reason(diags, {"a"}) is not None


def test_attempted_but_missing_diagnostic_is_dark():
    assert all_dark_reason({}, {"a"}) is not None


# ── 2 + 3. the real witness path, the status, the header ────────────────────

def _boom():
    raise ConnectionError("planted")


def _cycle(c, *sources_ok):
    """A stand-in collect that runs sources through the REAL isolation wrapper
    (attempted-set + diagnostics), then the REAL blind stamp + publish."""
    def run():
        for src in ("meshtasticd", "mqtt"):
            fn = _boom
            if src in sources_ok:
                def fn(src=src):
                    c._record_diagnostic(src, 1, 1, "ok")
                    return [FC["features"][0]]
            c._timed_collect_inline(src, fn)
        geo = {"type": "FeatureCollection", "features": [], "properties": {}}
        c._stamp_blind(geo)
        c._publish_snapshot(geo)
        return geo
    c.calls.publish = None
    return run


def test_an_all_dark_cycle_is_a_witnessed_failure(collector, monkeypatch):
    monkeypatch.setattr(collector, "_collect_locked", _cycle(collector))
    collector._run_collect_witnessed()
    st = collector.snapshot_status()
    assert st["blind_cycles"] == 1
    assert st["collect_failures"] == 0  # nothing RAISED (review F5)
    assert "every map source dark" in st["last_error"]
    assert st["state"] == "blind" and "meshtasticd unreachable" in st["blind_reason"]


def test_a_lit_cycle_after_a_dark_one_clears_it(collector, monkeypatch):
    monkeypatch.setattr(collector, "_collect_locked", _cycle(collector))
    collector._run_collect_witnessed()
    monkeypatch.setattr(collector, "_collect_locked", _cycle(collector, "mqtt"))
    collector._run_collect_witnessed()
    st = collector.snapshot_status()
    assert st["state"] == "ok" and st["last_error"] is None
    assert "blind_reason" not in st
    assert st["blind_cycles"] == 1  # the dark cycle stays counted


def test_attempted_set_is_per_cycle(collector, monkeypatch):
    # cycle 1 runs mqtt lit; cycle 2 runs ONLY meshtasticd, dark → blind
    monkeypatch.setattr(collector, "_collect_locked", _cycle(collector, "mqtt"))
    collector._run_collect_witnessed()

    def only_radio():
        collector._timed_collect_inline("meshtasticd", _boom)
        geo = {"type": "FeatureCollection", "features": [], "properties": {}}
        collector._stamp_blind(geo)
        collector._publish_snapshot(geo)
        return geo
    monkeypatch.setattr(collector, "_collect_locked", only_radio)
    collector._run_collect_witnessed()
    assert collector.snapshot_status()["state"] == "blind"


def test_geojson_and_status_say_blind_over_http(server, collector, monkeypatch):
    monkeypatch.setattr(collector, "_collect_locked", _cycle(collector))
    collector._run_collect_witnessed()
    status, headers, _, _ = _get(server, "/api/nodes/geojson")
    assert status == 200
    assert "every map source dark" in headers.get("X-Map-Blind", "")
    import json
    status, _, body, _ = _get(server, "/api/status")
    assert json.loads(body)["snapshot"]["state"] == "blind"


def test_a_lit_snapshot_sends_no_blind_header(server, collector, monkeypatch):
    monkeypatch.setattr(collector, "_collect_locked", _cycle(collector, "mqtt"))
    collector._run_collect_witnessed()
    _, headers, _, _ = _get(server, "/api/nodes/geojson")
    assert "X-Map-Blind" not in headers


def test_blind_header_is_one_safe_line():
    from utils._map_node_endpoints import blind_header_value
    v = blind_header_value("every map source dark: a\r\nSet-Cookie: x=1" + "z" * 500)
    assert "\r" not in v and "\n" not in v and len(v) <= 200 and v.isascii()


# ── 3b. the page reads the header ───────────────────────────────────────────

def test_page_shows_the_banner_from_the_blind_header():
    i = HTML.index("response.headers.get('X-Map-Blind')")
    block = HTML[i:i + 600]
    assert "showMapBlind(" in block
    # the blind check runs BEFORE the age check can clear the banner
    assert i < HTML.index("response.headers.get('X-Snapshot-Age')")


def test_the_real_collect_stamps_blind_before_it_publishes():
    # the stand-in cycles above call _stamp_blind themselves; pin that the REAL
    # collect does too, before the snapshot lands (verdict + data = ONE value)
    import inspect
    from utils.map_data_collector import MapDataCollector
    src = inspect.getsource(MapDataCollector._collect_locked)
    assert "self._stamp_blind(geojson)" in src
    assert src.index("self._stamp_blind(geojson)") < src.index("self._publish_snapshot(geojson)")


def test_a_deadline_skipped_source_still_votes(collector, monkeypatch):
    # review F2: cycle N+1 skips a source whose last fetch is still running;
    # it reads collect_timeout and must stay in the vote
    import threading
    alive = threading.Event()
    t = threading.Thread(target=alive.wait, args=(5,), daemon=True)
    t.start()
    try:
        collector.__dict__.setdefault("_late_source_workers", {})["meshtasticd"] = t

        def run():
            collector._timed_collect("meshtasticd", lambda: [FC["features"][0]])
            geo = {"type": "FeatureCollection", "features": [], "properties": {}}
            collector._stamp_blind(geo)
            collector._publish_snapshot(geo)
            return geo
        monkeypatch.setattr(collector, "_collect_locked", run)
        collector._run_collect_witnessed()
        assert "meshtasticd" in collector._cycle_attempted
        st = collector.snapshot_status()
        assert st["state"] == "blind" and "meshtasticd collect_timeout" in st["blind_reason"]
    finally:
        alive.set()
        t.join(5)


def test_a_replayed_cache_is_not_resaved():
    # review F4 (narrowed r2): re-saving the replayed cache re-stamps its mtime
    # (24 h expiry never trips). Gated on the REPLAY, not on blind. History
    # still records the replay (H14 design, test_peer_radio_heard) -- residual.
    import inspect
    from utils.map_data_collector import MapDataCollector
    src = inspect.getsource(MapDataCollector._collect_locked)
    save = src.index("self._save_cache(")
    assert "if not replayed_cache:" in src[src.rindex("\n", 0, save) - 200:save]


# ── round 2 (Fable, by running): a source that writes no diagnostic ─────────
# seam: _timed_collect_inline says THIS cycle's fact when the source returns
# without recording (direct_radio success, meshtasticd CLI fallback), and never
# lets LAST cycle's reason stand in for this one.

def _bare_timed():
    from utils.map_data_collector import MapDataCollector
    c = MapDataCollector.__new__(MapDataCollector)
    c._cycle_attempted = set()
    c._collect_deadline_at = None
    return c


def test_features_without_a_diagnostic_are_lit():
    c = _bare_timed()
    c._timed_collect_inline("direct_radio", lambda: [FC["features"][0]] * 2)
    d = c._source_diagnostics["direct_radio"]
    assert d["yielded"] == 2 and d["reason_if_zero"] == "ok"
    assert "_diag_pending" not in d
    assert all_dark_reason(c._source_diagnostics, c._cycle_attempted) is None


def test_last_cycles_dark_reason_never_hides_this_cycles_nodes():
    c = _bare_timed()
    c._source_diagnostics["meshtasticd"] = _d("unreachable")
    c._timed_collect_inline("meshtasticd", lambda: [FC["features"][0]] * 3)
    assert all_dark_reason(c._source_diagnostics, c._cycle_attempted) is None


def test_last_cycles_ok_never_lights_an_empty_silent_cycle():
    # the false-OK twin: prior "ok, 40" carried over, this cycle returned []
    c = _bare_timed()
    c._source_diagnostics["meshtasticd"] = _d("ok", 40)
    c._timed_collect_inline("meshtasticd", lambda: [])
    got = all_dark_reason(c._source_diagnostics, c._cycle_attempted)
    assert got is not None and "meshtasticd" in got
    assert c._source_diagnostics["meshtasticd"]["yielded"] == 0


def test_a_recorded_diagnostic_is_left_alone():
    c = _bare_timed()

    def src():
        c._record_diagnostic("mqtt", 0, 0, "no_data", "answered empty")
        return []
    c._timed_collect_inline("mqtt", src)
    d = c._source_diagnostics["mqtt"]
    assert d["reason_if_zero"] == "no_data" and d["notes"] == "answered empty"
    assert "_diag_pending" not in d


# ── round 2 F3: a DOWN service is not "off by design" ───────────────────────

class _Sub:
    def __init__(self, connected):
        self.connected = connected

    def is_connected(self):
        return self.connected

    def get_geojson(self):
        return {"type": "FeatureCollection", "features": []}


@pytest.mark.parametrize("sub,reason", [
    (_Sub(False), "unreachable"),     # broker down: a fault
    (_Sub(True), "no_data"),          # connected, empty: an answer
    (None, "not_configured"),         # no subscriber here at all
])
def test_mqtt_reason_says_down_vs_empty_vs_absent(tmp_path, monkeypatch, sub, reason):
    from utils import _map_collector_trackers as trk
    from utils.map_data_collector import MapDataCollector

    class _Hub:
        def get_local_subscriber(self):
            return sub
    monkeypatch.setattr(trk, "_hub", lambda: _Hub())
    c = MapDataCollector(cache_dir=tmp_path, config_dir=tmp_path, enable_history=False)
    assert c._collect_mqtt() == []
    assert c._source_diagnostics["mqtt"]["reason_if_zero"] == reason


@pytest.mark.parametrize("has_rns,reason", [(True, "unreachable"), (False, "not_configured")])
def test_rnsd_down_is_unreachable_when_rns_is_installed(tmp_path, monkeypatch, has_rns, reason):
    from utils import _map_collector_rns as rns_mod
    from utils import service_check
    from utils.map_data_collector import MapDataCollector
    monkeypatch.setattr(rns_mod, "_HAS_RNS", has_rns)
    monkeypatch.setattr(service_check, "check_rns_shared_instance", lambda **kw: False)
    c = MapDataCollector(cache_dir=tmp_path, config_dir=tmp_path, enable_history=False)
    assert c._collect_rns_direct() == []
    assert c._source_diagnostics["rns_direct"]["reason_if_zero"] == reason
