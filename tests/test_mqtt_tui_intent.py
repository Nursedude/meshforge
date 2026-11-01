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
