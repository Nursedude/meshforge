"""The map's Meshtastic "online" window = firmware's (operator 2026-11-08).

Firmware v2.7.26.54e0d8d src/mesh/NodeDB.cpp:1692:
    #define NUM_ONLINE_SECS (60 * 60 * 2) // 2 hrs to consider someone offline
The map drew a node Offline after 15 min while the radio called it online for
2 h. Nodes broadcast position / telemetry / nodeinfo every 15 min – 3 h, so a
healthy GPS node (kiai, "1h ago") sat red between broadcasts. Measured 11-08:
of 22 nodes the radio decoded within 2 h AND the map knew fresh, 13 drew online.

Scope: the MAP only. The gateway's UnifiedNodeTracker keeps its own window for
every network (named divergence, queued); the map re-derives a tracker
feature's is_online from its own last_heard so one rule draws every marker.
"""
import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from utils import map_data_collector as mdc
from utils.map_data_collector import MapDataCollector, FIRMWARE_ONLINE_WINDOW_S


def make(tmp_path, saved=None):
    home = tmp_path / "home"
    cfg = home / ".config" / "meshforge"
    cfg.mkdir(parents=True)
    if saved is not None:
        (cfg / "map_settings.json").write_text(json.dumps(saved))
    with patch("utils.map_data_collector.get_real_user_home", return_value=home):
        return MapDataCollector(cache_dir=tmp_path / "cache", enable_history=False,
                                config_dir=cfg)


def test_window_is_firmwares_two_hours():
    assert FIRMWARE_ONLINE_WINDOW_S == 2 * 60 * 60


def test_meshtastic_defaults_derive_from_the_one_constant():
    for name in ("DEFAULT_ONLINE_THRESHOLD_MINUTES", "DEFAULT_MESHTASTIC_THRESHOLD_MINUTES"):
        assert getattr(MapDataCollector, name) * 60 == FIRMWARE_ONLINE_WINDOW_S, name


def test_fresh_install_uses_two_hours(tmp_path):
    c = make(tmp_path)
    assert c.get_online_threshold_seconds() == FIRMWARE_ONLINE_WINDOW_S
    assert c.get_source_threshold_seconds("meshtastic") == FIRMWARE_ONLINE_WINDOW_S


def test_persisted_old_default_15_is_migrated(tmp_path):
    # the exact shape three fleet boxes carry (SettingsManager used to persist defaults)
    c = make(tmp_path, {"online_status_threshold_minutes": 15,
                        "meshtastic_threshold_minutes": 15,
                        "selected_region": "hawaii"})
    assert c.get_online_threshold_seconds() == FIRMWARE_ONLINE_WINDOW_S
    assert c.get_source_threshold_seconds("meshtastic") == FIRMWARE_ONLINE_WINDOW_S
    assert c._settings.get("selected_region") == "hawaii"


def test_a_deliberate_operator_value_is_kept(tmp_path):
    c = make(tmp_path, {"online_status_threshold_minutes": 30,
                        "meshtastic_threshold_minutes": 45})
    assert c.get_online_threshold_seconds() == 30 * 60
    assert c.get_source_threshold_seconds("meshtastic") == 45 * 60


def test_rns_and_aredn_windows_are_not_touched(tmp_path):
    c = make(tmp_path)
    assert c.get_source_threshold_seconds("rns") == 30 * 60
    assert c.get_source_threshold_seconds("aredn") == 60 * 60


@pytest.mark.parametrize("age_min, online", [(30, True), (119, True), (121, False)])
def test_node_heard_inside_two_hours_is_online(tmp_path, age_min, online):
    c = make(tmp_path)
    assert c._is_node_online(time.time() - age_min * 60, source="meshtastic") is online


def _real_tracker(*nodes):
    """A REAL UnifiedNodeTracker (cache load off) — review 11-08 F1: a mock
    invented the `last_heard` the real to_geojson never emitted."""
    from gateway.node_tracker import UnifiedNodeTracker
    from gateway.node_models import UnifiedNode
    with patch.object(UnifiedNodeTracker, "_load_cache"):
        t = UnifiedNodeTracker()
    for num, age_s in nodes:
        mesh = {"num": num, "user": {"id": f"!{num:08x}", "longName": f"n{num:x}"},
                "position": {"latitude": 19.43, "longitude": -155.2}}
        if age_s is not None:
            mesh["lastHeard"] = time.time() - age_s
        t.add_node(UnifiedNode.from_meshtastic(mesh))
    return t


def _collect(c, tracker, radio=(), mqtt=()):
    """collect() with only these sources answering — nothing live is touched."""
    real_timed = MapDataCollector._timed_collect

    def only(self, name, fn, *a, **kw):
        if name == "unified_tracker":
            return real_timed(self, name, fn, *a, **kw)
        return {"meshtasticd": list(radio), "mqtt": list(mqtt)}.get(name, [])

    with patch.object(mdc, "get_node_tracker", return_value=tracker), \
         patch.object(MapDataCollector, "_timed_collect", only), \
         patch.object(MapDataCollector, "_merge_federation", lambda self, f, pl, *a, **k: {}):
        geo = c.collect(max_age_seconds=0)
    return {f["properties"]["id"]: f["properties"] for f in geo["features"]}


def test_real_tracker_exports_its_heard_time():
    t = _real_tracker((0xAAAA0001, 1800))
    p = t.to_geojson()["features"][0]["properties"]
    assert p["last_heard"] == pytest.approx(time.time() - 1800, abs=5)


def test_tracker_node_heard_30_min_ago_is_online_on_the_map(tmp_path):
    # the tracker's own window (15 min) says offline; the map's (2 h) says online
    by = _collect(make(tmp_path), _real_tracker((0xAAAA0001, 1800)))
    assert by["!aaaa0001"]["is_online"] is True


def test_tracker_node_heard_3_h_ago_is_offline(tmp_path):
    by = _collect(make(tmp_path), _real_tracker((0xAAAA0002, 3 * 3600)))
    assert by["!aaaa0002"]["is_online"] is False


def test_tracker_node_without_a_heard_time_is_never_online(tmp_path):
    by = _collect(make(tmp_path), _real_tracker((0xAAAA0003, None)))
    assert by["!aaaa0003"]["is_online"] is False


def test_mqtt_node_is_judged_by_the_same_window(tmp_path):
    # the exact shape mqtt_subscriber.get_geojson emits (network meshtastic, via_mqtt)
    f = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-155.2, 19.4]},
         "properties": {"id": "!bbbb0001", "name": "m", "network": "meshtastic", "via_mqtt": True,
                        "is_online": False, "last_seen": "30m ago",
                        "last_heard": time.time() - 1800}}
    by = _collect(make(tmp_path), _real_tracker(), mqtt=[f])
    assert by["!bbbb0001"]["is_online"] is True


def test_mqtt_subscriber_exports_its_heard_time():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src",
                            "monitoring", "mqtt_subscriber.py"), encoding="utf-8").read()
    assert '"last_heard": node.last_seen.timestamp() if node.last_seen else None,' in src


def test_tcp_path_judges_with_the_window_the_label_names(tmp_path):
    c = make(tmp_path, {"meshtastic_threshold_minutes": 45})
    now = time.time()
    node = {"num": 0xCCCC0001, "user": {"id": "!cccc0001", "longName": "t"},
            "position": {"latitude": 19.4, "longitude": -155.2}, "lastHeard": now - 50 * 60}
    f = c._parse_tcp_node("!cccc0001", node, now)
    assert f["properties"]["is_online"] is False          # 50 min > the 45-min window
    node["lastHeard"] = now - 40 * 60
    assert c._parse_tcp_node("!cccc0001", node, now)["properties"]["is_online"] is True


# --- the page names the window it was sent (never a hard-coded word) ---------

import os  # noqa: E402
import re  # noqa: E402

_HTML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web",
                          "node_map.html"), encoding="utf-8").read()


def _js_body(name):
    m = re.search(r"function %s\(([^)]*)\)\s*\{" % name, _HTML)
    assert m, name
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(_HTML[i], 0)
        i += 1
    return _HTML[m.end():i]


def test_geojson_carries_the_meshtastic_window():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "utils",
                            "map_data_collector.py"), encoding="utf-8").read()
    assert '"meshtastic_online_window_minutes": self.get_source_threshold_seconds("meshtastic") // 60' in src


def test_status_text_reads_the_window_for_meshtastic_only():
    body = _js_body("nodeStatusText")
    assert "meshtastic_online_window_minutes" in _js_body("meshtasticWindowText")
    assert "props.network === 'meshtastic'" in body
    assert "'Not heard in '" in body


def test_popup_and_status_card_use_the_one_status_reader():
    assert "${props.is_online ? '● Online' : '○ Offline'}" not in _HTML
    assert "status.textContent = props.is_online ? 'Online' : 'Offline';" not in _HTML
    assert "nodeStatusText(props)" in _HTML[_HTML.index('<div class="info-label">Status</div>'):][:400]
    assert "status.textContent = nodeStatusText(props);" in _HTML


def test_window_text_is_null_when_not_sent():
    body = _js_body("meshtasticWindowText")
    assert "return null" in body and "isFinite" in body


def test_federated_node_never_borrows_this_boxs_window():
    # review 11-08 F6: a peer judged it by ITS window; this box only knows its own radio
    body = _js_body("nodeStatusText")
    fed = body[body.index("if (props.federated)"):]
    fed = fed[:fed.index(";") + 1]
    assert "meshtasticWindowText" not in fed and "this box" in fed
    assert body.index("if (props.federated)") < body.index("meshtasticWindowText()")


# --- review round 2 (Fable by running, 11-08): pin what 5 mutants survived ---

def test_a_source_saying_online_without_a_heard_time_is_overruled(tmp_path):
    c = make(tmp_path)
    for lh in (None, 0, time.time() - 3 * 3600):
        props = {"id": "mesh_!eeee0001", "network": "meshtastic", "is_online": True,
                 "last_heard": lh}
        c._normalize_meshtastic_props(props)
        assert props["is_online"] is False, lh


def test_both_comparisons_are_strict_at_the_window_edge(tmp_path):
    c = make(tmp_path)
    now = 1_800_000_000.0
    w = FIRMWARE_ONLINE_WINDOW_S
    with patch("time.time", return_value=now):
        assert c._is_node_online(now - w, source="meshtastic") is False
        assert c._is_node_online(now - w + 1, source="meshtastic") is True
    node = {"num": 0xCCCC0002, "user": {"id": "!cccc0002", "longName": "t"},
            "position": {"latitude": 19.4, "longitude": -155.2}, "lastHeard": now - w}
    assert c._parse_tcp_node("!cccc0002", node, now)["properties"]["is_online"] is False
    node["lastHeard"] = now - w + 1
    assert c._parse_tcp_node("!cccc0002", node, now)["properties"]["is_online"] is True


def test_mqtt_marker_is_judged_by_the_meshtastic_window_not_another(tmp_path):
    c = make(tmp_path, {"meshtastic_threshold_minutes": 45})
    props = {"id": "!bbbb0002", "network": "meshtastic", "via_mqtt": True,
             "is_online": True, "last_heard": time.time() - 50 * 60}
    c._normalize_meshtastic_props(props)
    assert props["is_online"] is False          # 50 min > the 45-min meshtastic window


def test_collector_built_without_settings_still_judges(tmp_path):
    # review round 2 F1: tests (and any __new__ path) build a collector with no _settings
    c = MapDataCollector.__new__(MapDataCollector)
    assert c.get_source_threshold_seconds("meshtastic") == FIRMWARE_ONLINE_WINDOW_S
    assert c.get_online_threshold_seconds() == FIRMWARE_ONLINE_WINDOW_S


# --- review round 3 (Fable by running, 11-08) --------------------------------

def test_node_cache_entry_keeps_its_heard_time_through_collect(tmp_path):
    # r3 Medium: the cache's ISO last_seen was dropped -> blanket "Not heard in 2 h"
    from datetime import datetime, timedelta
    c = make(tmp_path)
    entry = {"id": "mesh_!dddd0002", "network": "meshtastic", "name": "cached",
             "latitude": 19.4, "longitude": -155.2,
             "last_seen": (datetime.now() - timedelta(minutes=10)).isoformat()}
    cached = c._node_cache_to_feature(entry)
    real_timed = MapDataCollector._timed_collect

    def only(self, name, fn, *a, **kw):
        return [cached] if name == "node_tracker" else []

    with patch.object(MapDataCollector, "_timed_collect", only), \
         patch.object(MapDataCollector, "_merge_federation", lambda self, f, pl, *a, **k: {}):
        geo = c.collect(max_age_seconds=0)
    p = {f["properties"]["id"]: f["properties"] for f in geo["features"]}["!dddd0002"]
    assert p["is_online"] is True


def test_tcp_drain_uses_the_meshtastic_window_not_the_global(tmp_path):
    # r3 M6 survivor: pin the window lookup in the real drain, not _parse_tcp_node
    c = make(tmp_path, {"online_status_threshold_minutes": 30, "meshtastic_threshold_minutes": 45})
    iface = MagicMock()
    iface.nodes = {"!cccc0003": {"num": 0xCCCC0003, "user": {"id": "!cccc0003", "longName": "t"},
                                 "position": {"latitude": 19.4, "longitude": -155.2},
                                 "lastHeard": time.time() - 40 * 60}}
    feats, _, _ = c._drain_interface_nodes(iface, "meshtasticd")
    assert feats[0]["properties"]["is_online"] is True     # 40 min < 45 (meshtastic), > 30 (global)


def test_an_id_less_meshtastic_feature_is_not_given_an_id(tmp_path):
    c = make(tmp_path)
    props = {"network": "meshtastic", "is_online": True}
    c._normalize_meshtastic_props(props)
    assert "id" not in props
