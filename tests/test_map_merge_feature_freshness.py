"""MapDataCollector._merge_feature moves freshness as ONE unit (2026-11-04).

When a fresher copy of a node arrives (MQTT merging into an already
collected feature), last_heard and is_online moved to it but the display
text last_seen stayed the OLD copy's ("199d ago" beside is_online=True).
Twin of the cross_protocol_collapse fix (honest_failure_modes #5): the
fields that say WHEN a node was heard must come from the same copy.
"""

from utils.map_data_collector import MapDataCollector

NOW = 1_793_830_000.0


def _feat(*, last_seen, last_heard, is_online):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-155.2, 19.4]},
        "properties": {
            "id": "!9666541f", "name": "meshforge base1",
            "last_seen": last_seen, "last_heard": last_heard,
            "is_online": is_online,
        },
    }


def _collector(tmp_path):
    return MapDataCollector(cache_dir=tmp_path, config_dir=tmp_path,
                            enable_history=False)


def test_fresher_copy_brings_its_last_seen_text(tmp_path):
    existing = _feat(last_seen="199d ago", last_heard=NOW - 199 * 86400,
                     is_online=False)
    new = _feat(last_seen="5m ago", last_heard=NOW - 300, is_online=True)
    _collector(tmp_path)._merge_feature(existing, new)
    p = existing["properties"]
    assert p["last_heard"] == NOW - 300
    assert p["is_online"] is True
    assert p["last_seen"] == "5m ago"


def test_older_copy_changes_no_freshness_field(tmp_path):
    # Control: an OLDER copy must not touch any of the three.
    existing = _feat(last_seen="2m ago", last_heard=NOW - 120, is_online=True)
    new = _feat(last_seen="3d ago", last_heard=NOW - 3 * 86400,
                is_online=False)
    _collector(tmp_path)._merge_feature(existing, new)
    p = existing["properties"]
    assert (p["last_seen"], p["last_heard"], p["is_online"]) == (
        "2m ago", NOW - 120, True)


def test_r1_F5_fresher_copy_with_placeholder_text_keeps_good_text(tmp_path):
    # Review r1 F5: _make_feature writes "unknown" when no text is passed;
    # a placeholder is not a reading and must not overwrite real text.
    existing = _feat(last_seen="2h ago", last_heard=NOW - 7200, is_online=False)
    new = _feat(last_seen="unknown", last_heard=NOW - 60, is_online=True)
    _collector(tmp_path)._merge_feature(existing, new)
    p = existing["properties"]
    assert p["last_heard"] == NOW - 60
    assert p["last_seen"] != "unknown"


def test_gap_fill_of_other_fields_unchanged(tmp_path):
    # Control: the fill-gaps rule for non-freshness fields still holds.
    existing = _feat(last_seen="2m ago", last_heard=NOW - 120, is_online=True)
    existing["properties"]["hardware"] = ""
    new = _feat(last_seen="3d ago", last_heard=NOW - 3 * 86400,
                is_online=False)
    new["properties"]["hardware"] = "RAK4631"
    _collector(tmp_path)._merge_feature(existing, new)
    assert existing["properties"]["hardware"] == "RAK4631"
