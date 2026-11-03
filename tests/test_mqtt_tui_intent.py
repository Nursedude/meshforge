"""MQTT-CFG S3 — the TUI edits saved-settings INTENT (plan r2, Q7).

Configure edits Root Topic + Channel + TLS (no free topic); every save goes
through one boundary that refuses with the field named, else shows what the
subscriber WILL subscribe to (the same derivation the subscriber runs); the
status screen shows the LIVE subscriptions and the saved file's state.
"""
import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context  # noqa: E402
from monitoring import mqtt_settings as ms  # noqa: E402
from utils.paths import atomic_write_text_preserving as W  # noqa: E402


def _handler():
    from handlers.mqtt import MQTTHandler
    ctx = make_handler_context()
    h = MQTTHandler()
    h.set_context(ctx)
    return h, ctx


def _msgboxes(ctx):
    return [c[1] for c in ctx.dialog.calls if c[0] == 'msgbox']


SAVED = {**ms.defaults(), "auto_start": True, "auto_start_telemetry": False}


# F8 state model (2026-11-02): these drive the REAL handler against a REAL
# file in a tmp config dir and read the file back — the screens send changes
# through mqtt_settings.save_changes, so a mocked save call proves nothing.
@pytest.fixture
def cfg(tmp_path):
    with patch("monitoring.mqtt_subscriber.mqtt_config_dir", return_value=tmp_path):
        yield tmp_path


def _seed(d, intent):
    assert ms.save(d, dict(intent), W).ok


def _saved(d):
    return json.loads((d / ms.FILENAME).read_text())


def test_configure_edits_root_and_channel_not_a_topic(cfg):
    _seed(cfg, SAVED)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["root", "channel", "save"]
    ctx.dialog._inputbox_returns = ["msh/EU_868/2/e", "MediumFast"]
    h._configure_mqtt()
    saved = _saved(cfg)
    assert (saved["root_topic"], saved["channel"]) == ("msh/EU_868/2/e", "MediumFast")
    assert "topic" not in saved and "use_tls" not in saved
    menu = [c for c in ctx.dialog.calls if c[0] == 'menu'][0]
    tags = [t for t, _ in menu[1][2]]
    assert "topic" not in tags and {"root", "channel", "tls"} <= set(tags)


def test_tls_row_opens_the_tls_menu_and_says_what_auto_means(cfg):
    # F8 Q6: the row used to CYCLE silently (auto -> on -> off); it now opens
    # the same menu as the private form. Cancel still discards.
    _seed(cfg, SAVED)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["tls", "on", "tls", "off", "cancel"]
    h._configure_mqtt()
    rows = [dict(c[1][2])["tls"] for c in ctx.dialog.calls
            if c[0] == 'menu' and c[1][0] == "MQTT Configuration"]
    assert "auto -> on" in rows[0]           # 8883 → TLS on
    assert rows[1].endswith("on") and "auto" not in rows[1]
    assert rows[2].endswith("off")
    tls = [c for c in ctx.dialog.calls if c[0] == 'menu' and c[1][0] == "TLS"]
    assert "on for port 8883" in dict(tls[0][1][2])["auto"]
    assert ms.load(cfg).intent["tls"] == "auto"   # cancel discards


def test_a_bad_root_is_refused_with_the_field_named_and_not_saved(cfg):
    _seed(cfg, SAVED)
    before = (cfg / ms.FILENAME).read_bytes()
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["root", "save", "cancel"]
    ctx.dialog._inputbox_returns = ["msh/HI"]
    h._configure_mqtt()
    assert (cfg / ms.FILENAME).read_bytes() == before
    (title, body) = [(m[0], m[1]) for m in _msgboxes(ctx) if m[0] == "Not Saved"][0]
    assert "root_topic" in body


def test_local_preset_saves_the_local_root_and_keeps_auto_start(cfg):
    _seed(cfg, SAVED)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["local"]
    with patch.object(type(h), "_detect_local_channel", return_value=None):
        h._configure_mqtt()
    saved = ms.load(cfg)
    assert saved.state == "ok"
    i = saved.intent
    assert (i["broker"], i["root_topic"], i["channel"]) == ("localhost", "msh/2/e", "+")
    # presets used to RESET auto-start silently; they keep the operator's choice
    assert (i["auto_start"], i["auto_start_telemetry"]) == (True, False)


def test_a_save_shows_what_the_subscriber_will_subscribe_to(cfg):
    _seed(cfg, SAVED)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["public"]
    h._configure_mqtt()
    saved = ms.load(cfg).intent
    shown = "\n".join(str(c) for c in ctx.dialog.calls)
    for t in ms.subscriptions(ms.subscriber_config(saved)):
        assert t in shown


def test_status_shows_the_live_subscriptions_and_the_saved_state(tmp_path):
    h, ctx = _handler()

    class _Sub:
        def is_connected(self):
            return True

        def get_stats(self):
            return {}

        def subscriptions(self):
            return ["msh/XX/2/json/Live/#", "msh/XX/2/e/Live/#"]

    h._mqtt_subscriber = _Sub()
    (tmp_path / ms.FILENAME).write_text("{broken")
    with patch("monitoring.mqtt_subscriber.mqtt_config_dir", return_value=tmp_path):
        h._show_mqtt_status()
    body = _msgboxes(ctx)[-1][1]
    assert "msh/XX/2/json/Live/#" in body and "msh/XX/2/e/Live/#" in body
    assert "UNREADABLE" in body


# --- review of f09fd86d..47d236f8 (2026-11-01) --------------------------------

def test_F5_an_unusable_detected_channel_falls_back_to_every_channel(cfg):
    # _detect_local_channel can return a longName fragment (09-20 class)
    _seed(cfg, SAVED)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["local"]
    with patch.object(type(h), "_detect_local_channel", return_value='Meshtastic 8d30",'):  # a SPACE: not a segment
        h._configure_mqtt()
    assert _saved(cfg)["channel"] == "+"


def test_F6_auto_start_on_an_unreadable_file_is_loud_not_silent(tmp_path, caplog):
    import logging
    h, _ = _handler()
    (tmp_path / ms.FILENAME).write_text('{"schema_version": 1, "auto_start": true')
    with patch("monitoring.mqtt_subscriber.mqtt_config_dir", return_value=tmp_path), \
         caplog.at_level(logging.ERROR):
        h.on_startup()
    assert h._mqtt_subscriber is None
    assert any("unreadable" in r.getMessage() and r.levelno >= logging.ERROR
               for r in caplog.records)


def test_F3_custom_broker_form_suggests_only_roots_the_owner_accepts():
    # review F3 (MA S5 pass, 2026-11-01): the custom-broker Root Topic prompt
    # offered "msh (all regions)", which validate() refuses — the form
    # suggested a value that can never be applied to the subscriber.
    from handlers.broker import BrokerHandler
    h = BrokerHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    ctx.dialog._inputbox_returns = [
        "my_broker", "mqtt.example.com", "1883", "", "US", "LongFast", "msh/US/2/e"]
    with patch("handlers.broker.save_profiles", return_value=True), \
         patch.object(h, "_apply_profile_to_mqtt"):
        h._setup_custom_broker()
    prompt = next(c[1][1] for c in ctx.dialog.calls
                  if c[0] == "inputbox" and c[1][0] == "Root Topic")
    suggested = [ln.split()[0].replace("{region}", "US")
                 for ln in prompt.splitlines() if ln.startswith("  ") and ln.strip()]
    assert suggested, prompt
    for root in suggested:
        assert ms.validate({**ms.defaults(), "root_topic": root}) == [], root


# mqtt-private-form-resets-tls (F8 review r3 #6 / r5 #4, 2026-11-01): the
# private form built its intent from defaults() and dropped `tls` — a saved
# tls=on became "auto", which on 1883 is OFF: the password in PLAINTEXT.
# Carrying the old value instead (TLS review r1 #1) would follow the operator
# to a broker it was never chosen for. The form now ASKS, for this broker:port.
PRIVATE = ["mqtt.example.com", "1883", "alice", "s3cret", "msh/US/2/e", "LongFast"]


def _private(cfg, saved_tls, tls_answer, inputs=PRIVATE):
    # the saved account is alice@old.example.org, so the form ASKS for the
    # new account's password (F8 Q7) — the 4th input answers it
    _seed(cfg, {**ms.defaults(), "broker": "old.example.org", "port": 8883,
                "username": "alice", "password": "s3cret", "tls": saved_tls,
                "auto_start": True})
    before = (cfg / ms.FILENAME).read_bytes()
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["private", tls_answer]
    ctx.dialog._inputbox_returns = list(inputs)
    h._configure_mqtt()
    tls_menu = [c[1] for c in ctx.dialog.calls if c[0] == 'menu' and c[1][0] == "TLS"]
    wrote = (cfg / ms.FILENAME).read_bytes() != before
    return ms.load(cfg).intent, tls_menu, wrote


def test_the_private_form_asks_tls_and_saves_the_answer(cfg):
    for answer, want in (("on", True), ("off", False), ("auto", "auto")):
        out, menu, _ = _private(cfg, "auto", answer)
        assert menu, "the form must ASK about TLS"
        assert out["tls"] == want, answer


def test_an_old_brokers_tls_never_follows_to_the_new_broker(cfg):
    # TLS review r1 #1: tls=False saved for old.example.org must not ride onto
    # a new 8883 broker (plaintext password); the answer is what is saved.
    out, _, _ = _private(cfg, False, "auto",
                         inputs=["mqtt.example.com", "8883", "alice", "s3cret",
                                 "msh/US/2/e", "LongFast"])
    assert out["tls"] == "auto" and ms.effective_tls(out) is True


def test_esc_at_tls_saves_nothing(cfg):
    _, menu, wrote = _private(cfg, "auto", None)
    assert menu and not wrote


def test_fields_the_form_does_not_ask_are_carried_not_reset(cfg):
    # TLS review r1 #3 (altitude): build from the config, not defaults().
    out, _, wrote = _private(cfg, "auto", "auto")
    assert wrote and out["auto_start"] is True


# 2026-11-01 live drill on the manager box: the Local preset saved channel
# 'NodeA",' — _detect_local_channel took the FIRST line containing "name"
# of `meshtastic --info`, which is the node's "longName" in "Nodes in mesh"
# (printed BEFORE "Channels:"), and the validator accepted the fragment.
# Fixture shape = the pinned meshtastic 2.7.11 source: showInfo() prints
# Owner / My info / Nodes in mesh (json, indent) first; node.showInfo() then
# showChannels(): "  Index N: ROLE psk=<x> <one-line json, ALL fields>".
INFO_FIXTURE = '''
Connected to radio

Owner: NodeA (NODA)
My info: { "myNodeNum": 305419896, "rebootCount": 3 }

Nodes in mesh: {
  "!12345678": {
    "num": 305419896,
    "user": {
      "id": "!12345678",
      "longName": "NodeA",
      "shortName": "NODA"
    }
  }
}

Preferences: { "device": { "role": "CLIENT" } }

Module preferences: { "mqtt": { "enabled": true } }

Channels:
  Index 0: PRIMARY psk=secret { "channelNum": 0, "psk": "c2VjcmV0", "name": "Regional", "id": 0, "uplinkEnabled": true, "downlinkEnabled": false, "moduleSettings": { "positionPrecision": 13, "isClientMuted": false } }
  Index 1: SECONDARY psk=default { "channelNum": 0, "psk": "AQ==", "name": "Other", "id": 0, "uplinkEnabled": false, "downlinkEnabled": false, "moduleSettings": { "positionPrecision": 0, "isClientMuted": false } }

Primary channel URL: <redacted>
'''


class _Done:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


def _fake_subprocess(rc=0, out=INFO_FIXTURE, raises=None):
    """A stand-in for the HANDLER module's `subprocess` name only — never the
    shared module (a patch of `mod.subprocess.run` patches it for everyone)."""
    import subprocess as real
    from unittest.mock import MagicMock
    m = MagicMock()
    m.SubprocessError, m.TimeoutExpired = real.SubprocessError, real.TimeoutExpired
    m.run.side_effect = raises if raises else (lambda *a, **k: _Done(rc, out))
    return m


def test_the_shared_parser_reads_channel_0_not_a_node_long_name():
    from utils.meshtastic_info import parse_primary_channel_name
    assert parse_primary_channel_name(INFO_FIXTURE) == "Regional"
    assert parse_primary_channel_name(
        INFO_FIXTURE.replace('"name": "Regional"', '"name": ""')) == ""
    assert parse_primary_channel_name(INFO_FIXTURE.split("Channels:")[0]) is None


def test_mqtt_detector_reads_the_primary_channel_through_the_shared_parser():
    h, _ = _handler()
    with patch('handlers.mqtt.subprocess', _fake_subprocess()):
        assert h._detect_local_channel() == "Regional"


def test_mqtt_detector_says_unnamed_vs_unread_apart(caplog):
    import logging
    import subprocess as real
    h, _ = _handler()
    unnamed = INFO_FIXTURE.replace('"name": "Regional"', '"name": ""')
    with patch('handlers.mqtt.subprocess', _fake_subprocess(out=unnamed)):
        assert h._detect_local_channel() == ""
    for fake in (_fake_subprocess(rc=1), _fake_subprocess(out="no channels here"),
                 _fake_subprocess(raises=real.TimeoutExpired("meshtastic", 15)),
                 _fake_subprocess(raises=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"))):
        caplog.clear()
        with caplog.at_level(logging.WARNING), patch('handlers.mqtt.subprocess', fake):
            assert h._detect_local_channel() is None
        assert any(r.levelno >= logging.WARNING for r in caplog.records)


def _local_preset(cfg, found):
    _seed(cfg, SAVED)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["local"]
    with patch.object(h, "_detect_local_channel", return_value=found):
        h._configure_mqtt()
    shown = " ".join(str(c[1]) for c in ctx.dialog.calls)
    return _saved(cfg)["channel"], shown


def test_local_preset_says_which_channel_claim_it_is_making(cfg):
    # "+" because unnamed and "+" because unreadable are different claims (hfm #2).
    for found, want_ch, want_note in (("Regional", "Regional", "read from the radio"),
                                      ("", "+", "unnamed"),
                                      (None, "+", "could NOT read")):
        ch, shown = _local_preset(cfg, found)
        assert ch == want_ch and want_note in shown, found


def test_service_menu_detector_uses_the_shared_parser():
    from handlers.service_menu import ServiceMenuHandler
    h = ServiceMenuHandler()
    h.set_context(make_handler_context())
    with patch('handlers.service_menu.subprocess', _fake_subprocess()):
        assert h._auto_detect_primary_channel() == "Regional"
    unnamed = INFO_FIXTURE.replace('"name": "Regional"', '"name": ""')
    with patch('handlers.service_menu.subprocess', _fake_subprocess(out=unnamed)):
        assert h._auto_detect_primary_channel() is None     # display: every channel


# tui-menu-default-item PART B (2026-11-02), re-cut for the F8 state model
# the same day. Rows never reorder; the SAVED row is marked "(saved)"; the
# HIGHLIGHTED row is the screen's current value for the SAME endpoint. Part B
# could not honour an unsaved TLS-row edit (r1 #1 built, reverted on r2 #3:
# a carried value was indistinguishable from an edit, so a stale form value
# could revert another writer's save). Screens now send only CHANGES, so an
# edit is an edit — the unsaved edit IS honoured (F8 grill Q1/Q2) and the old
# "disk only" test is inverted below. An endpoint edit still never borrows a
# TLS value; a legacy file's converted tls is never claimed "(saved)".

SAME = ["mqtt.example.com", "1883", "alice", "msh/US/2/e", "LongFast"]   # keep pw: no input


def _form(cfg, inputs=SAME, list_script=("private",), tls_answer="ENTER"):
    """Run Configure -> (list_script) with title-routed menus: Password ->
    keep, Settings Unreadable -> back, TLS -> tls_answer ("ENTER" = the
    highlighted row, else row 1 — what the real binary does)."""
    h, ctx = _handler()
    fake_menu = ctx.dialog.menu
    script = list(list_script)
    answers = list(tls_answer) if isinstance(tls_answer, (list, tuple)) else [tls_answer]
    seen = []

    def menu(title, text, choices, **kw):
        fake_menu(title, text, choices, **kw)          # records + phantom check
        if title == "TLS":
            seen.append((choices, kw.get('default_item')))
            ans = answers.pop(0) if answers else "ENTER"
            if ans == "ENTER":
                return kw.get('default_item') or choices[0][0]
            return ans
        if title == "Password":
            return "keep"
        if title == "Settings Unreadable":
            return "back"
        return script.pop(0) if script else None
    ctx.dialog.menu = menu
    ctx.dialog._inputbox_returns = list(inputs)
    h._configure_mqtt()
    return seen


def _disk(broker="mqtt.example.com", port=1883, tls=True):
    return {**ms.defaults(), "broker": broker, "port": port, "tls": tls,
            "username": "alice", "password": "s3cret"}


def test_same_endpoint_highlights_the_saved_tls_without_reordering(cfg):
    for saved, tag in ((True, "on"), (False, "off"), ("auto", "auto")):
        _seed(cfg, _disk(tls=saved))
        (choices, default), = _form(cfg)
        assert [t for t, _ in choices] == ["auto", "on", "off"], "row order is stable"
        assert default == tag, (saved, default)
        assert "(saved)" in dict(choices)[tag]


def test_same_endpoint_match_normalises_host_case_and_trailing_dot(cfg):
    # (the form refuses a host with spaces, so whitespace never reaches it)
    _seed(cfg, _disk(broker="MQTT.Example.com."))
    (choices, default), = _form(cfg)
    assert default == "on" and "(saved)" in dict(choices)["on"]


def test_an_unsaved_tls_edit_is_honoured_and_the_saved_row_still_marked(cfg):
    # INVERTED by the F8 grill (was: "disk only, the unsaved edit is not
    # honoured"). TLS row edit off (unsaved), then the private form: the
    # screen's value is highlighted, the disk's is marked, Enter saves the edit.
    _seed(cfg, _disk(tls=True))
    choices, default = _form(cfg, list_script=("tls", "private"),
                             tls_answer=["off", "ENTER"])[-1]
    assert default == "off" and "(saved)" in dict(choices)["on"]
    assert _saved(cfg)["tls"] is False


def test_an_unsaved_endpoint_edit_never_borrows_a_tls(cfg):
    # the private form moves to mqtt.example.com; the saved tls belongs to
    # old.example.org and must not follow (and the new account is asked its
    # password — the 4th input)
    _seed(cfg, _disk(broker="old.example.org", tls=True))
    (choices, default), = _form(cfg, inputs=PRIVATE)
    assert default is None and not any("(saved)" in d for _, d in choices)
    assert _saved(cfg)["tls"] == "auto"


def test_a_different_endpoint_or_no_saved_file_gets_no_default(cfg, tmp_path):
    for disk, inputs in ((_disk(broker="old.example.org"), PRIVATE),
                         (_disk(port=8883), SAME), (None, PRIVATE)):
        (cfg / ms.FILENAME).unlink(missing_ok=True)
        if disk:
            _seed(cfg, disk)
        (choices, default), = _form(cfg, inputs=inputs)
        assert default is None, disk
        assert not any("(saved)" in d for _, d in choices), disk


def test_unreadable_saved_file_gets_no_default(cfg):
    (cfg / ms.FILENAME).write_text("{boom")
    (choices, default), = _form(cfg, inputs=PRIVATE)
    assert default is None and not any("(saved)" in d for _, d in choices)
    assert (cfg / ms.FILENAME).read_text() == "{boom"      # Back: nothing written


def test_every_saved_tls_value_maps_to_a_menu_tag():
    # review r1 #1 of part A: a stale default degrades to row 1 SILENTLY in
    # the real binary — so a tag rename must fail HERE. The fake also
    # refuses a phantom default_item.
    from handlers import _mqtt_configure as m
    tags = [t for t, _ in m._tls_menu_rows("b.example", 1883)]
    for saved in ("auto", True, False):
        assert m._tls_tag(saved) in tags, saved
    assert m._tls_tag(1) is None and m._tls_tag(0) is None and m._tls_tag(None) is None


def test_enter_through_saves_the_saved_tls_for_the_same_endpoint(cfg):
    for saved in (True, False, "auto"):
        _seed(cfg, {**_disk(tls=saved), "channel": "Before"})
        (choices, default), = _form(cfg)
        assert _saved(cfg)["tls"] == saved, saved
        assert _saved(cfg)["channel"] == "LongFast", "nothing saved: vacuous"
        assert any("(saved)" in d for t, d in choices if t == default)


def test_a_legacy_files_converted_tls_is_never_claimed_saved(cfg):
    (cfg / ms.FILENAME).write_text(json.dumps(
        {"broker": "mqtt.example.com", "port": 1883, "topic": "msh/US/2/e/LongFast/#",
         "username": "alice", "password": "s3cret"}))
    (choices, _default), = _form(cfg)
    assert not any("(saved)" in d for _, d in choices)


def test_the_auto_row_uses_the_subscribers_tls_rule():
    from handlers import _mqtt_configure as m
    with patch('monitoring.mqtt_settings.effective_tls', return_value=True):
        assert "on for port 1883" in dict(m._tls_menu_rows("b.example", 1883))["auto"]
    with patch('monitoring.mqtt_settings.effective_tls', return_value=False):
        assert "off for port 8883" in dict(m._tls_menu_rows("b.example", 8883))["auto"]


def test_the_auto_row_asks_the_rule_with_the_real_broker():
    # r2 #8: a partial intent would drift the moment effective_tls reads more
    from handlers import _mqtt_configure as m
    seen = []
    with patch('monitoring.mqtt_settings.effective_tls',
               side_effect=lambda i: seen.append(dict(i)) or False):
        m._tls_menu_rows("b.example", 1883)
    assert seen == [{"broker": "b.example", "port": 1883, "tls": "auto"}]
