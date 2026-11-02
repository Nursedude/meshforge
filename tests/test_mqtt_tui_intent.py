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


def _handler():
    from handlers.mqtt import MQTTHandler
    ctx = make_handler_context()
    h = MQTTHandler()
    h.set_context(ctx)
    return h, ctx


def _msgboxes(ctx):
    return [c[1] for c in ctx.dialog.calls if c[0] == 'msgbox']


SAVED = {**ms.defaults(), "auto_start": True, "auto_start_telemetry": False}


@patch('handlers.mqtt.load_mqtt_config', return_value=dict(SAVED))
@patch('handlers.mqtt.save_mqtt_config', return_value=True)
def test_configure_edits_root_and_channel_not_a_topic(save, _load):
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


@patch('handlers.mqtt.load_mqtt_config', return_value=dict(SAVED))
@patch('handlers.mqtt.save_mqtt_config', return_value=True)
def test_tls_row_cycles_auto_on_off_and_says_what_auto_means(save, _load):
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["tls", "tls", "tls", "cancel"]
    h._configure_mqtt()
    rows = [dict(c[1][2])["tls"] for c in ctx.dialog.calls if c[0] == 'menu']
    assert "auto -> on" in rows[0]           # 8883 → TLS on
    assert rows[1].endswith("on") and "auto" not in rows[1]
    assert rows[2].endswith("off")
    assert "auto" in rows[3]                 # back round to auto
    assert not save.called                   # cancel discards


@patch('handlers.mqtt.load_mqtt_config', return_value=dict(SAVED))
@patch('handlers.mqtt.save_mqtt_config', return_value=True)
def test_a_bad_root_is_refused_with_the_field_named_and_not_saved(save, _load):
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["root", "save", "cancel"]
    ctx.dialog._inputbox_returns = ["msh/HI"]
    h._configure_mqtt()
    assert not save.called
    (title, body) = [(m[0], m[1]) for m in _msgboxes(ctx) if m[0] == "Not Saved"][0]
    assert "root_topic" in body


@patch('handlers.mqtt.load_mqtt_config', return_value=dict(SAVED))
@patch('handlers.mqtt.save_mqtt_config', return_value=True)
def test_local_preset_saves_the_local_root_and_keeps_auto_start(save, _load):
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["local"]
    with patch.object(type(h), "_detect_local_channel", return_value=None):
        h._configure_mqtt()
    saved = save.call_args[0][0]
    assert (saved["broker"], saved["root_topic"], saved["channel"]) == ("localhost", "msh/2/e", "+")
    # presets used to RESET auto-start silently; they keep the operator's choice
    assert (saved["auto_start"], saved["auto_start_telemetry"]) == (True, False)
    assert ms.validate(saved) == []


@patch('handlers.mqtt.load_mqtt_config', return_value=dict(SAVED))
@patch('handlers.mqtt.save_mqtt_config', return_value=True)
def test_a_save_shows_what_the_subscriber_will_subscribe_to(save, _load):
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

@patch('handlers.mqtt.load_mqtt_config', return_value=dict(SAVED))
@patch('handlers.mqtt.save_mqtt_config', return_value=True)
def test_F5_an_unusable_detected_channel_falls_back_to_every_channel(save, _load):
    # _detect_local_channel can return a longName fragment (09-20 class)
    h, ctx = _handler()
    ctx.dialog._menu_returns = ["local"]
    with patch.object(type(h), "_detect_local_channel", return_value='Meshtastic 8d30",'):
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
    with patch('handlers.mqtt.load_mqtt_config', return_value=dict(saved)), \
         patch('handlers.mqtt.save_mqtt_config', return_value=True) as save:
        h, ctx = _handler()
        ctx.dialog._menu_returns = ["private", tls_answer]
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
