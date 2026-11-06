"""The :5000 map must never draw a healthy picture when it cannot see.

Two defects from the 2026-11-06 maps audit (H1, H2; doc "MeshForge Maps (:5000)
— Deep Research, Audit & Plan"), re-read by hand before this test was written:

H1  Any failed or non-OK ``/api/nodes/geojson`` fetch (including the cold-start
    503 "warming") fell through to built-in DEMO nodes -- an online "Honolulu
    Gateway" at 85 % battery -- and the marker diff then REMOVED the real
    nodes. The cure: demo data only behind ``?demo=1``; on a failed fetch keep
    the last good markers and say the map is blind.

H2  Diagnostic reasons the browser didn't list (collect_timeout,
    lock_contended, stale_cached, deferred_to_gateway, slow_sysinfo) rendered
    as healthy: ``computeHealth`` skipped them and returned ``ok``, and the
    source list mapped them to the green ``ok`` dot. The cure is a CLOSED
    vocabulary (hfm #7): ONE Python table, a JS copy pinned to it here, and an
    unknown reason rendered ``unobservable`` -- never ``ok``.

The JS is pinned by reading node_map.html (no JS runtime on the fleet boxes),
the same house style as test_map_marker_icon_key.py.
"""
import json
import logging
import os
import re

import pytest

from utils.map_source_reasons import (
    SOURCE_REASON_STATE,
    STATES,
    reason_state,
)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
HTML = open(os.path.join(ROOT, "web", "node_map.html"), encoding="utf-8").read()
SRC_UTILS = os.path.join(ROOT, "src", "utils")


def _body(name: str) -> str:
    m = re.search(r"function %s\(([^)]*)\)\s*\{" % name, HTML)
    assert m, f"{name} not found in node_map.html"
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(HTML[i], 0)
        i += 1
    return HTML[m.end():i]


def _js_reason_table() -> dict:
    m = re.search(r"const SOURCE_REASON_STATE = (\{.*?\});", HTML, re.S)
    assert m, "node_map.html carries no SOURCE_REASON_STATE table"
    # The literal is written as strict JSON so it can be compared exactly.
    return json.loads(m.group(1))


# ---------------------------------------------------------------- H2: vocabulary

def test_js_reason_table_is_the_python_table():
    """Two consumers of one vocabulary share ONE constant (hfm #5)."""
    assert _js_reason_table() == SOURCE_REASON_STATE


def test_every_state_is_known():
    assert set(SOURCE_REASON_STATE.values()) <= set(STATES)
    assert "unobservable" in STATES
    assert "unobservable" not in SOURCE_REASON_STATE.values(), (
        "unobservable is the UNKNOWN-reason answer, not a table entry")


def _emitted_reason_literals() -> set:
    """Every reason string a collector can hand _record_diagnostic."""
    found = set()
    pat = re.compile(
        r"""(?:reason_if_zero\s*=\s*|\breason\s*=\s*|entry\["reason_if_zero"\]\s*=\s*)"""
        r"""(?:None\s+if\s+\w+\s+else\s+|)['"]([a-z_]+)['"]"""
        r"""(?:\s+if\s+[^\n]*?else\s+['"]([a-z_]+)['"])?""")
    for fn in os.listdir(SRC_UTILS):
        if not (fn.startswith("_map_collector_") or fn == "map_data_collector.py"):
            continue
        text = open(os.path.join(SRC_UTILS, fn), encoding="utf-8").read()
        for a, b in pat.findall(text):
            found.add(a)
            if b:
                found.add(b)
    return found


def test_every_reason_a_collector_emits_is_in_the_table():
    emitted = _emitted_reason_literals()
    # A planted sanity floor: the scan must SEE the reasons the audit named,
    # or an empty scan would pass this test vacuously.
    assert {"collect_timeout", "lock_contended", "stale_cached",
            "deferred_to_gateway", "slow_sysinfo", "unreachable"} <= emitted
    missing = emitted - set(SOURCE_REASON_STATE)
    assert not missing, f"collector reasons with no state: {sorted(missing)}"


@pytest.mark.parametrize("reason", ["collect_timeout", "lock_contended",
                                    "stale_cached", "slow_sysinfo", "unreachable"])
def test_a_failing_source_is_never_ok(reason):
    assert reason_state(reason) != "ok"


def test_unknown_reason_is_unobservable_never_ok():
    assert reason_state("some_new_reason") == "unobservable"
    assert reason_state(None) == "unobservable"
    assert reason_state("") == "unobservable"


def test_record_diagnostic_warns_on_a_reason_outside_the_vocabulary(caplog):
    """hfm #3: a reason the author cannot have meant fails LOUD at the boundary."""
    from utils.map_data_collector import MapDataCollector
    c = MapDataCollector.__new__(MapDataCollector)
    c._source_diagnostics = {}
    with caplog.at_level(logging.WARNING, logger="utils.map_data_collector"):
        c._record_diagnostic("x", attempted=1, yielded=0, reason_if_zero="typo_reason")
    assert c._source_diagnostics["x"]["reason_if_zero"] == "typo_reason"
    assert any("typo_reason" in r.getMessage() for r in caplog.records)


def test_record_diagnostic_is_quiet_for_known_reasons(caplog):
    from utils.map_data_collector import MapDataCollector
    c = MapDataCollector.__new__(MapDataCollector)
    c._source_diagnostics = {}
    with caplog.at_level(logging.WARNING, logger="utils.map_data_collector"):
        c._record_diagnostic("x", attempted=1, yielded=0, reason_if_zero="collect_timeout")
    assert not [r for r in caplog.records if "vocabulary" in r.getMessage()]


def test_compute_health_reads_the_table_not_a_private_list():
    body = _body("computeHealth")
    assert "reasonState(" in body
    # The old private allow-list (unknown reasons fell through to ok) is gone.
    assert "'no_positions'" not in body and "'unreachable'" not in body
    # Unobservable joins the warnings, it never counts as ok.
    assert re.search(r"state === 'unobservable'", body) or "'unobservable'" in body


def test_reason_state_defaults_to_unobservable_in_js():
    body = _body("reasonState")
    assert "SOURCE_REASON_STATE" in body and "'unobservable'" in body


def test_source_list_never_maps_an_unknown_reason_to_the_ok_dot():
    body = _body("renderSourcesList")
    seg = body[body.index("for (const src of allSources)"):]
    seg = seg[:seg.index("list.innerHTML")]
    assert "? reason : 'ok'" not in seg, "unknown reason still drawn as the green ok dot"
    assert "reasonState(" in seg
    assert "d.reason_if_zero || 'ok'" not in seg


def test_every_dot_class_the_list_can_draw_has_css():
    for state in STATES:
        assert re.search(r"\.source-dot\.%s\b" % state, HTML), f"no CSS for dot '{state}'"


# ---------------------------------------------------------------- H1: no demo lie

def test_demo_nodes_load_only_behind_the_demo_param():
    body = _body("refreshNodes")
    assert "Honolulu Gateway" not in body, "demo features still inline in refreshNodes"
    calls = [m.start() for m in re.finditer(r"loadNodes\(DEMO_GEOJSON\)", body)]
    assert len(calls) == 1
    guard = body[:calls[0]]
    guard = guard[guard.rindex("if ("):]
    assert "has('demo')" in guard


def test_a_failed_fetch_says_blind_and_keeps_the_last_good_markers():
    body = _body("refreshNodes")
    tail = body[body.index("updateApiStatus(false"):]
    assert "showMapBlind(" in tail
    # After the API leg fails, nothing may replace the markers on screen
    # except the explicit ?data= / ?demo=1 developer paths.
    stray = [line.strip() for line in tail.splitlines()
             if "loadNodes(" in line
             and "DEMO_GEOJSON" not in line and "params.get('data')" not in line]
    assert not stray, f"a failed fetch still replaces markers: {stray}"


def test_a_good_fetch_clears_the_blind_state():
    body = _body("refreshNodes")
    ok = body[body.index("if (response.ok)"):]
    ok = ok[:ok.index("return;")]
    assert "clearMapBlind(" in ok


def test_blind_banner_names_since_and_reason():
    body = _body("showMapBlind")
    assert "blindSince" in body and "reason" in body
    assert "map-blind" in body


def test_blind_markers_are_visibly_greyed():
    assert re.search(r"\.map-blind\s+\.leaflet-marker-pane", HTML)


# ------------------------------------------- round-1 non-author review fixes

def _bare_collector():
    from utils.map_data_collector import MapDataCollector
    c = MapDataCollector.__new__(MapDataCollector)
    c._source_diagnostics = {}
    return c


def test_every_dot_class_any_row_draws_has_css():
    """r1 F1: the claw rows draw `unreachable`; dropping its rule blanked them."""
    for dot in set(re.findall(r"dot: '([a-z_]+)'", _body("clawDisplayState"))):
        assert re.search(r"\.source-dot\.%s\b" % dot, HTML), f"claw dot '{dot}' has no CSS"


def test_contributing_nothing_keeps_its_own_dot():
    """r1 F6: no_positions / no_data are ok for the badge, but the dot still
    says the source contributes nothing to the map."""
    seg = _body("renderSourcesList")
    assert "reason === 'no_positions' || reason === 'no_data'" in seg
    for dot in ("no_positions", "no_data"):
        assert re.search(r"\.source-dot\.%s\b" % dot, HTML)


def test_mid_collect_stub_holds_the_last_reason():
    """r1 F2: /api/status reads diagnostics without the collect lock; the
    pre-stamp must not erase the last cycle's reason while a source runs."""
    c = _bare_collector()
    c._record_diagnostic("aredn", attempted=3, yielded=3)
    seen = {}

    def fn():
        seen.update(c._source_diagnostics["aredn"])
        c._record_diagnostic("aredn", attempted=3, yielded=2)
        return []

    c._timed_collect("aredn", fn)
    assert seen.get("reason_if_zero") == "ok", seen
    assert c._source_diagnostics["aredn"]["yielded"] == 2


def test_a_raising_source_reports_zero_not_last_cycles_count():
    c = _bare_collector()
    c._record_diagnostic("aredn", attempted=50, yielded=50)

    def boom():
        raise RuntimeError("x")

    c._timed_collect("aredn", boom)
    d = c._source_diagnostics["aredn"]
    assert d["reason_if_zero"] == "unreachable"
    assert d["yielded"] == 0 and d["attempted"] == 0


def test_unknown_reason_warns_once_per_source_and_reason(caplog):
    """r1 F4: a collect runs every ~30 s; one new reason must not log forever."""
    c = _bare_collector()
    with caplog.at_level(logging.WARNING, logger="utils.map_data_collector"):
        for _ in range(5):
            c._record_diagnostic("x", attempted=1, yielded=0, reason_if_zero="typo_reason")
        c._record_diagnostic("y", attempted=1, yielded=0, reason_if_zero="typo_reason")
    hits = [r for r in caplog.records if "typo_reason" in r.getMessage()]
    assert len(hits) == 2


def test_developer_payloads_after_a_failed_fetch_still_say_blind():
    """r1 F3: ?data= / ?demo=1 replace the markers — the banner must say the
    map is not showing live nodes."""
    body = _body("refreshNodes")
    tail = body[body.index("updateApiStatus(false"):]
    for marker in ("params.get('data')", "loadNodes(DEMO_GEOJSON)"):
        seg = tail[tail.index(marker):]
        seg = seg[:seg.index("return;")]
        assert "showMapBlind(" in seg, f"{marker} path does not say blind"


def test_blind_banner_wording_and_reach():
    """r1 F8/F9/F10: one 'retrying', overlays greyed too, a date when not today."""
    assert "cold start) — retrying'" not in HTML
    assert re.search(r"\.map-blind\s+\.leaflet-overlay-pane", HTML)
    assert "toLocaleDateString(" in _body("showMapBlind")
