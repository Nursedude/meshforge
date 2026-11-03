"""MQTT-CFG S3 — the TUI edits saved-settings INTENT (plan r2, Q7).

Configure edits Root Topic + Channel + TLS (no free topic); every save goes
through one boundary that refuses with the field named, else shows what the
subscriber WILL subscribe to (the same derivation the subscriber runs); the
status screen shows the LIVE subscriptions and the saved file's state.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context  # noqa: E402
from monitoring import mqtt_settings as ms  # noqa: E402
from mqtt_tui_seam import real_settings, write_failed  # noqa: E402,F401


def _handler():
    from handlers.mqtt import MQTTHandler
    ctx = make_handler_context()
    h = MQTTHandler()
    h.set_context(ctx)
    return h, ctx


def _msgboxes(ctx):
    return [c[1] for c in ctx.dialog.calls if c[0] == 'msgbox']


SAVED = {**ms.defaults(), "auto_start": True, "auto_start_telemetry": False}


def test_configure_edits_root_and_channel_not_a_topic():
    with real_settings(dict(SAVED)) as (save, _cfg):
        h, ctx = _handler()
        ctx.dialog._menu_returns = ["root", "channel", "save"]
        ctx.dialog._inputbox_returns = ["msh/EU_868/2/e", "MediumFast"]
        h._configure_mqtt()
        saved = save.call_args[0][0]
        assert (saved["root_topic"], saved["channel"]) == ("msh/EU_868/2/e", "MediumFast")
        assert "topic" not in saved and "use_tls" not in saved
        menu = [c for c in ctx.dialog.calls if c[0] == 'menu'][0]
        tags = [t for t, _ in menu[1][2]]
        assert "topic" not in tags and {"root", "channel", "tls"} <= set(tags)


def test_tls_row_says_what_auto_means_and_opens_the_one_tls_menu():
    # Q6 (F8 v2): the Configure row no longer cycles — it opens the same TLS
    # menu as the private form; Esc there changes nothing; Cancel saves nothing.
    with real_settings(dict(SAVED)) as (save, _cfg):
        h, ctx = _handler()
        ctx.dialog._menu_returns = ["tls", None, "cancel"]
        h._configure_mqtt()
        rows = [dict(c[1][2])["tls"] for c in ctx.dialog.calls
                if c[0] == 'menu' and c[1][0] == "MQTT Configuration"]
        assert "auto -> on" in rows[0] and rows[1] == rows[0]   # 8883 -> TLS on
        assert [c[1][0] for c in ctx.dialog.calls if c[0] == 'menu'].count("TLS") == 1
        assert not save.called


def test_a_bad_root_is_refused_with_the_field_named_and_not_saved():
    with real_settings(dict(SAVED)) as (save, _cfg):
        h, ctx = _handler()
        ctx.dialog._menu_returns = ["root", "save", "cancel"]
        ctx.dialog._inputbox_returns = ["msh/HI"]
        h._configure_mqtt()
        # the OWNER refuses it (F8 v2: one write path) — nothing reaches disk
        assert save.call_args[0][0]["root_topic"] == "msh/HI"
        assert ms.load(_cfg).intent["root_topic"] == SAVED["root_topic"]
        (title, body) = [(m[0], m[1]) for m in _msgboxes(ctx) if m[0] == "Not Saved"][0]
        assert "root_topic" in body


def test_local_preset_saves_the_local_root_and_keeps_auto_start():
    with real_settings(dict(SAVED)) as (save, _cfg):
        h, ctx = _handler()
        ctx.dialog._menu_returns = ["local"]
        with patch.object(type(h), "_detect_local_channel", return_value=None):
            h._configure_mqtt()
        saved = save.call_args[0][0]
        assert (saved["broker"], saved["root_topic"], saved["channel"]) == ("localhost", "msh/2/e", "+")
        # presets used to RESET auto-start silently; they keep the operator's choice
        assert (saved["auto_start"], saved["auto_start_telemetry"]) == (True, False)
        assert ms.validate(saved) == []


def test_a_save_shows_what_the_subscriber_will_subscribe_to():
    with real_settings(dict(SAVED)) as (save, _cfg):
        h, ctx = _handler()
        ctx.dialog._menu_returns = ["public"]
        h._configure_mqtt()
        saved = save.call_args[0][0]
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

def test_F5_an_unusable_detected_channel_falls_back_to_every_channel():
    with real_settings(dict(SAVED)) as (save, _cfg):
        # _detect_local_channel can return a longName fragment (09-20 class)
        h, ctx = _handler()
        ctx.dialog._menu_returns = ["local"]
        with patch.object(type(h), "_detect_local_channel", return_value='Meshtastic 8d30",'):  # a SPACE: not a segment
            h._configure_mqtt()
        assert save.called and save.call_args[0][0]["channel"] == "+"


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


def _private(saved_tls, tls_answer, inputs=PRIVATE):
    saved = {**ms.defaults(), "broker": "old.example.org", "port": 8883,
             "username": "alice", "password": "s3cret", "tls": saved_tls,
             "auto_start": True}
    with real_settings(saved) as (save, _cfg), \
         patch('handlers.mqtt._saved_tls_for_endpoint', return_value=None):
        h, ctx = _handler()
        ctx.dialog._menu_returns = ["private", "new", tls_answer]
        ctx.dialog._inputbox_returns = list(inputs)
        h._configure_mqtt()
    tls_menu = [c[1] for c in ctx.dialog.calls if c[0] == 'menu' and c[1][0] == "TLS"]
    return save, tls_menu


def test_the_private_form_asks_tls_and_saves_the_answer():
    for answer, want in (("on", True), ("off", False), ("auto", "auto")):
        save, menu = _private("auto", answer)
        assert menu, "the form must ASK about TLS"
        assert save.call_args[0][0]["tls"] == want, answer


def test_an_old_brokers_tls_never_follows_to_the_new_broker():
    # TLS review r1 #1: tls=False saved for old.example.org must not ride onto
    # a new 8883 broker (plaintext password); the answer is what is saved.
    save, _ = _private(False, "auto",
                       inputs=["mqtt.example.com", "8883", "alice", "s3cret",
                               "msh/US/2/e", "LongFast"])
    out = save.call_args[0][0]
    assert out["tls"] == "auto" and ms.effective_tls(out) is True


def test_esc_at_tls_saves_nothing():
    save, menu = _private("auto", None)
    assert menu and not save.called


def test_fields_the_form_does_not_ask_are_carried_not_reset():
    # TLS review r1 #3 (altitude): build from the config, not defaults().
    save, _ = _private("auto", "auto")
    assert save.call_args[0][0]["auto_start"] is True


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


def _local_preset(found):
    with real_settings(SAVED) as (save, _cfg):
        h, ctx = _handler()
        ctx.dialog._menu_returns = ["local"]
        with patch.object(h, "_detect_local_channel", return_value=found):
            h._configure_mqtt()
    shown = " ".join(str(c[1]) for c in ctx.dialog.calls)
    return save.call_args[0][0]["channel"], shown


def test_local_preset_says_which_channel_claim_it_is_making():
    # "+" because unnamed and "+" because unreadable are different claims (hfm #2).
    for found, want_ch, want_note in (("Regional", "Regional", "read from the radio"),
                                      ("", "+", "unnamed"),
                                      (None, "+", "could NOT read")):
        ch, shown = _local_preset(found)
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


# tui-menu-default-item PART B (2026-11-02): Enter-through on the SAME
# endpoint must keep the SAVED tls. The default is read from the ON-DISK
# settings (load_settings), never the form's config dict — the parked
# current-first follow-up regressed on exactly that (an unsaved Broker-row
# edit made the old endpoint's TLS look current). Rows never reorder.

def _tls_call(menu_calls):
    (title, _text, choices), kwargs = menu_calls[0]
    return [t for t, _ in choices], kwargs.get('default_item'), choices


def _private_disk(disk, tls_answer, inputs=PRIVATE, config=None):
    from monitoring.mqtt_settings import Loaded
    cfg = config if config is not None else (disk or ms.defaults())
    loaded = Loaded(state="ok", intent=dict(disk)) if disk else Loaded(state="absent")
    with real_settings(cfg) as (save, _cfg), \
         patch('monitoring.mqtt_subscriber.load_settings', return_value=loaded):
        h, ctx = _handler()
        ctx.dialog._menu_returns = ["private", "new", tls_answer]
        ctx.dialog._inputbox_returns = list(inputs)
        h._configure_mqtt()
    calls = [(c[1], c[2]) for c in ctx.dialog.calls if c[0] == 'menu' and c[1][0] == "TLS"]
    return save, calls


def _disk(broker="mqtt.example.com", port=1883, tls=True):
    return {**ms.defaults(), "broker": broker, "port": port, "tls": tls,
            "username": "alice", "password": "s3cret"}


def test_same_endpoint_highlights_the_saved_tls_without_reordering():
    for saved, tag in ((True, "on"), (False, "off"), ("auto", "auto")):
        _, calls = _private_disk(_disk(tls=saved), tag)
        tags, default, _ = _tls_call(calls)
        assert tags == ["auto", "on", "off"], "row order is stable"
        assert default == tag, (saved, default)


def test_same_endpoint_match_normalises_host_case_and_trailing_dot():
    # (the form refuses a host with spaces, so whitespace never reaches it)
    _, calls = _private_disk(_disk(broker="MQTT.Example.com."), "on")
    assert _tls_call(calls)[1] == "on"


def test_default_comes_from_disk_not_the_unsaved_form_config():
    # The parked regression: config says broker=mqtt.example.com tls=True
    # (an unsaved in-menu edit) while DISK holds another endpoint.
    disk = _disk(broker="old.example.org", port=8883, tls=False)
    cfg = _disk(tls=True)
    _, calls = _private_disk(disk, "auto", config=cfg)
    assert _tls_call(calls)[1] is None


def test_a_different_endpoint_or_no_saved_file_gets_no_default():
    for disk in (_disk(broker="old.example.org"), _disk(port=8883), None):
        _, calls = _private_disk(disk, "auto")
        assert _tls_call(calls)[1] is None, disk


def test_unreadable_saved_file_gets_no_default():
    from monitoring.mqtt_settings import Loaded
    bad = Loaded(state="unreadable", error="boom")
    with real_settings(ms.defaults()), \
         patch('monitoring.mqtt_subscriber.load_settings', return_value=bad):
        h, ctx = _handler()
        ctx.dialog._menu_returns = ["private", "new", "auto"]
        ctx.dialog._inputbox_returns = list(PRIVATE)
        h._configure_mqtt()
    calls = [(c[1], c[2]) for c in ctx.dialog.calls if c[0] == 'menu' and c[1][0] == "TLS"]
    assert _tls_call(calls)[1] is None


def test_every_saved_tls_value_maps_to_a_menu_tag():
    # review r1 #1 of part A: a stale default degrades to row 1 SILENTLY in
    # the real binary — so a tag rename must fail HERE. The fake also
    # refuses a phantom default_item.
    from handlers import mqtt as m
    tags = [t for t, _ in m._tls_menu_rows("b.example", 1883)]
    for saved in ("auto", True, False):
        assert m._tls_tag(saved) in tags, saved
    assert m._tls_tag(1) is None and m._tls_tag(0) is None and m._tls_tag(None) is None


# Part B review r1+r2 (2026-11-02, contextless non-author passes).
# The default is the DISK value only. r1 #1 (honour an unsaved TLS-row edit)
# was built, then REVERTED on r2 #3: a stale form value would revert another
# writer's save while claiming to be the operator's edit. Telling an edit from
# a carried value needs dirty-field state -> the F8 ownership grill. An
# unsaved ENDPOINT edit never borrows a TLS value (the parked regression). #2: a legacy file's
# converted tls is never claimed as "(saved)". #3: the auto label uses the
# subscriber's own rule. #7: Enter is simulated end to end (the fake returns
# the default_item), so a highlight/save mismatch fails a test.

def _enter_through(disk, config, inputs=PRIVATE, legacy=False):
    from monitoring.mqtt_settings import Loaded
    loaded = Loaded(state="ok", intent=dict(disk), legacy=legacy) if disk \
        else Loaded(state="absent")
    with real_settings(config) as (save, _cfg), \
         patch('monitoring.mqtt_subscriber.load_settings', return_value=loaded):
        h, ctx = _handler()
        fake_menu = ctx.dialog.menu
        seen = []

        def menu(title, text, choices, **kw):
            if title == "TLS":                      # Enter = the highlighted row
                seen.append((choices, kw.get('default_item')))
                fake_menu(title, text, choices, **kw)
                return kw.get('default_item') or choices[0][0]
            return fake_menu(title, text, choices, **kw)
        ctx.dialog.menu = menu
        ctx.dialog._menu_returns = ["private", "new"]
        ctx.dialog._inputbox_returns = list(inputs)
        h._configure_mqtt()
    return save, seen


def test_enter_through_saves_the_saved_tls_for_the_same_endpoint():
    for saved in (True, False, "auto"):
        save, seen = _enter_through(_disk(tls=saved), _disk(tls=saved))
        assert save.call_args[0][0]["tls"] == saved, saved
        choices, default = seen[0]
        assert any("(saved)" in d for t, d in choices if t == default)


def test_an_unsaved_tls_edit_is_not_claimed_the_disk_value_is_highlighted():
    # DECISION (r2 #3): disk only. The form's unsaved tls=False is NOT
    # honoured on Enter-through (pre-existing; F8 grill) — and nothing is
    # ever labelled an "unsaved edit" it might not be.
    save, seen = _enter_through(_disk(tls=True), _disk(tls=False))
    choices, default = seen[0]
    assert default == "on" and "(saved)" in dict(choices)["on"]
    assert save.call_args[0][0]["tls"] is True
    assert not any("unsaved" in d for _, d in choices)


def test_an_unsaved_endpoint_edit_never_borrows_a_tls():
    # config moved to mqtt.example.com (unsaved) carrying the OLD endpoint's tls
    save, seen = _enter_through(_disk(broker="old.example.org", tls=True),
                                _disk(tls=True))
    assert seen[0][1] is None and save.call_args[0][0]["tls"] == "auto"


def test_no_saved_file_means_no_default_even_if_the_form_matches():
    save, seen = _enter_through(None, _disk(tls=True))
    assert seen[0][1] is None and save.call_args[0][0]["tls"] == "auto"


def test_a_legacy_files_converted_tls_is_never_claimed_saved():
    _, seen = _enter_through(_disk(tls="auto"), _disk(tls="auto"), legacy=True)
    choices, default = seen[0]
    assert default is None and not any("(saved)" in d for _, d in choices)


def test_the_auto_row_uses_the_subscribers_tls_rule():
    from handlers import mqtt as m
    with patch('monitoring.mqtt_settings.effective_tls', return_value=True):
        assert "on for port 1883" in dict(m._tls_menu_rows("b.example", 1883))["auto"]
    with patch('monitoring.mqtt_settings.effective_tls', return_value=False):
        assert "off for port 8883" in dict(m._tls_menu_rows("b.example", 8883))["auto"]


def test_the_auto_row_asks_the_rule_with_the_real_broker():
    # r2 #8: a partial intent would drift the moment effective_tls reads more
    from handlers import mqtt as m
    seen = []
    with patch('monitoring.mqtt_settings.effective_tls',
               side_effect=lambda i: seen.append(dict(i)) or False):
        m._tls_menu_rows("b.example", 1883)
    assert seen == [{"broker": "b.example", "port": 1883, "tls": "auto"}]
