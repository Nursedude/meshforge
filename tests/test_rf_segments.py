"""BIC-2 cannot_hear — the DECLARED RF segment per fleet box (operator 11-16).

Why declared, not measured: Linux meshtasticd serves no /json/report (the
map's /api/status ``radio_config`` reads available:false on a Pi, measured
11-16; ESP32-only, upstream FR #9164), the journal names channels not
presets, and asking the radio is a PhoneAPI client (it force-closed the map
twice, 11-11). So the segment is an operator DECLARATION, the page says
"declared", and a declaration that cannot be read is LOUD, never silence.
"""
import json

import pytest

from utils import inbox_heard, inbox_legs, rf_segments

LF = "meshtastic:LONG_FAST/ch20"
ST = "meshtastic:SHORT_TURBO/ch8"


def _write(tmp_path, doc):
    p = tmp_path / rf_segments.CONFIG_BASENAME
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return p


def test_absent_file_is_inert(tmp_path):
    d = rf_segments.load(tmp_path / rf_segments.CONFIG_BASENAME)
    assert d.segments == {} and d.send_segment is None and d.error is None


def test_a_good_declaration_loads(tmp_path):
    d = rf_segments.load(_write(tmp_path, {
        "send_from": "center-box", "segments": {"center-box": LF, "moc3": ST, "moc": LF}}))
    assert d.error is None
    assert d.send_segment == LF
    assert d.segments == {"center-box": LF, "moc3": ST, "moc": LF}


def test_comment_keys_are_allowed(tmp_path):
    d = rf_segments.load(_write(tmp_path, {
        "//": "note", "send_from": "a", "segments": {"a": LF}}))
    assert d.error is None


@pytest.mark.parametrize("doc,why", [
    ("{not json", "unreadable"),
    ([], "object"),
    ({"segments": {"a": LF}}, "send_from"),
    ({"send_from": "a", "segments": {}}, "no segments"),
    ({"send_from": "a", "segments": None}, "segments"),
    ({"send_from": "b", "segments": {"a": LF}}, "send_from"),
    ({"send_from": "a", "segments": {"a": "LongFast"}}, "a"),
    ({"send_from": "a", "segments": {"a": "meshtastic:NOT_A_PRESET/ch20"}}, "preset"),
    ({"send_from": "a", "segments": {"a": "meshtastic:LONG_FAST/chX"}}, "a"),
    ({"send_from": "a", "segments": {"a": LF}, "segmnets": {}}, "unknown key"),
])
def test_a_declaration_the_author_cannot_have_meant_is_loud(tmp_path, doc, why):
    """hfm #3: refuse loudly, never absorb into 'no segments'."""
    d = rf_segments.load(_write(tmp_path, doc))
    assert d.segments == {} and d.send_segment is None
    assert d.error and why in d.error, d.error


def test_an_unreadable_file_is_loud(tmp_path):
    p = _write(tmp_path, {"send_from": "a", "segments": {"a": LF}})
    p.chmod(0)
    try:
        d = rf_segments.load(p)
        assert d.segments == {} and d.error and "unreadable" in d.error
    finally:
        p.chmod(0o600)


# ── review r1 (11-16, Fable by running): NOT CLEAN ──

@pytest.mark.parametrize("raw", [
    b'{"//":"caf\xe9","send_from":"a","segments":{"a":"meshtastic:LONG_FAST/ch20"}}',
    '{"send_from":"a","segments":{"a":"meshtastic:LONG_FAST/ch20"}}'.encode("utf-16"),
    b"[" * 200000,
])
def test_h1_a_file_that_is_not_utf8_json_is_loud_never_raises(tmp_path, raw):
    """H1: UnicodeDecodeError / RecursionError escaped load() and crashed the inbox read."""
    p = tmp_path / rf_segments.CONFIG_BASENAME
    p.write_bytes(raw)
    d = rf_segments.load(p)
    assert d.segments == {} and d.error, d


def test_l4_a_utf8_bom_is_accepted(tmp_path):
    p = tmp_path / rf_segments.CONFIG_BASENAME
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"send_from": "a", "segments": {"a": LF}}).encode())
    assert rf_segments.load(p).error is None


@pytest.mark.parametrize("raw,why", [
    ('{"send_from":"a","segments":{"a":"%s","a":"%s"}}' % (LF, ST), "duplicate"),
    ('{"send_from":"a","send_from":"b","segments":{"a":"%s","b":"%s"}}' % (LF, LF), "duplicate"),
])
def test_m3_duplicate_keys_are_refused_not_last_one_wins(tmp_path, raw, why):
    d = rf_segments.load(_write(tmp_path, raw))
    assert d.segments == {} and d.error and why in d.error, d


@pytest.mark.parametrize("alias", ["moc3 ", " moc 3", "", "-x", "a/b"])
def test_m1_an_alias_the_target_list_could_never_hold_is_refused(tmp_path, alias):
    d = rf_segments.load(_write(tmp_path, {"send_from": "a", "segments": {"a": LF, alias: ST}}))
    assert d.segments == {} and d.error, d


@pytest.mark.parametrize("value", [
    "meshtastic:LONG_FAST/ch0",          # 0 = hashed from the channel name: not a slot
    "meshtastic:LONG_FAST/ch999",        # past the band's slot count: wraps silently
    "meshtastic:LONG_FAST/ch20\n",       # `$` admitted a trailing newline
    "meshtastic:VERY_LONG_SLOW/ch1",     # the firmware's display name is "Invalid"
])
def test_m2_a_segment_that_names_no_single_real_slot_is_refused(value):
    assert rf_segments.parse_segment(value) is None


def test_m2_a_slot_is_canonical_so_equal_slots_compare_equal():
    assert rf_segments.parse_segment("meshtastic:LONG_FAST/ch020") == LF


def test_the_shipped_template_loads_through_the_real_parser():
    """The template and the parser are one contract (hfm #5)."""
    import pathlib
    t = pathlib.Path(__file__).parent.parent / "templates" / "rf_segments_declared.json.template"
    d = rf_segments.load(t)
    assert d.error is None, d.error
    assert d.send_segment == LF and len(d.segments) == 2


def test_the_declared_preset_is_one_the_firmware_knows():
    """One table: meshtastic_modem. A preset it does not know is refused."""
    from utils import meshtastic_modem
    for name, shown in meshtastic_modem.PRESET_DISPLAY_NAMES.items():
        ok = rf_segments.parse_segment(f"meshtastic:{name}/ch1") is not None
        assert ok == (shown != "Invalid"), name   # VERY_LONG_SLOW: the firmware says "Invalid"
    assert rf_segments.parse_segment("meshtastic:FAST_LONG/ch1") is None


# ── the consumer: a box on the other DECLARED preset reads cannot_hear ──

def test_a_box_on_the_other_declared_preset_reads_cannot_hear():
    sections = {"moc3": {"entries": [], "covered_since": 0, "covered_until": 9e9,
                         "last_line_ts": 9e9}}
    out = inbox_legs.heard_from(0x1234, 1000.0, sections, now=5000.0,
                                segments={"moc3": ST}, send_segment=LF)
    assert out["summary"]["cannot_hear"] == ["moc3"]
    assert "on another preset/slot (declared" in out["line"]
    assert "new id" in out["line"]


def test_an_undeclared_box_is_never_cannot_hear():
    sections = {"kiai": None}
    out = inbox_legs.heard_from(0x1234, 1000.0, sections, now=5000.0,
                                segments={"moc3": ST}, send_segment=LF)
    assert out["summary"].get("cannot_hear", []) == []
    assert inbox_heard.STATES  # vocabulary unchanged
