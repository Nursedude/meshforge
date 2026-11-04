"""F8 v2 round 6 — broker SETUP flows say every field they write (r5b #1–#4).

Record: ``.claude/plans/mqtt_f8_state_model_2026_11_02.md`` § r5b (Fable 5.1
non-author pass on 1006c842). Operator rulings 2026-11-03: #3 = placeholder
+ explicit reveal; #4 = Esc abandons, BLANK takes the value shown.

- #1 Custom setup's box named Broker/Channel/Topic only, but the profile it
  wrote also carries the account and a DERIVED TLS flag (port == 8883).
- #2 A setup whose profile name already exists replaced it silently and said
  "created" (Custom, and Private — whose password nodes depend on).
- #3 Radio MQTT Setup rendered the STORED profile password in plaintext;
  Private setup rendered the one just typed.
- #4 Public/Private treated a blank answer as Esc; Custom (r5) takes the default.
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
from utils.broker_profiles import (  # noqa: E402
    create_custom_profile, create_private_profile, create_public_profile,
    get_meshtastic_mqtt_setup_argv, get_meshtastic_mqtt_setup_commands,
)

PLACEHOLDER = "<MQTT_PASSWORD>"


@pytest.fixture
def cfg(tmp_path):
    d = tmp_path / "meshforge"
    d.mkdir()
    with patch("monitoring.mqtt_subscriber.mqtt_config_dir", return_value=d), \
            patch("handlers.mqtt.MQTTHandler._detect_local_channel", return_value=None):
        yield d


def _broker():
    from handlers.broker import BrokerHandler
    ctx = make_handler_context()
    h = BrokerHandler()
    h.set_context(ctx)
    return h, ctx


def _calls(ctx, kind, title=None):
    return [c for c in ctx.dialog.calls
            if c[0] == kind and (title is None or c[1][0] == title)]


def _text(ctx, title):
    (text,) = [c[1][1] for c in _calls(ctx, "msgbox", title)]
    return text


class _Store:
    """In-memory broker profile store (never the operator's real file)."""

    def __init__(self, profiles=None):
        self.profiles = dict(profiles or {})
        self.saves = []

    def load(self):
        return dict(self.profiles)

    def save(self, p):
        self.saves.append(dict(p))
        self.profiles = dict(p)
        return True


def _run(store, flow, inputs=(), yes=()):
    with patch("handlers.broker.load_profiles", side_effect=store.load), \
            patch("handlers.broker.save_profiles", side_effect=store.save), \
            patch("handlers.broker.check_mosquitto_installed",
                  return_value=(True, "ok")):
        h, ctx = _broker()
        ctx.dialog._inputbox_returns = list(inputs)
        ctx.dialog._yesno_returns = list(yes)
        getattr(h, flow)()
    return ctx


CUSTOM_TLS = ["mine", "mqtt.b.example", "8883", "bob", "bobpw", "US", "LongFast", ""]
CUSTOM_ANON = ["mine", "mqtt.b.example", "1883", "", "US", "LongFast", ""]


# --- #1 the Custom "Saved" box names every field it wrote ------------------

def test_r6_1_custom_summary_names_account_password_state_and_tls(cfg):
    store = _Store()
    ctx = _run(store, "_setup_custom_broker", CUSTOM_TLS)
    text = _text(ctx, "Custom Broker Saved")
    assert store.profiles["mine"].use_tls is True
    assert "Account: bob@mqtt.b.example" in text
    assert "Password: set" in text
    assert "TLS: on" in text and "8883" in text
    assert "bobpw" not in repr(ctx.dialog.calls)


def test_r6_1_custom_summary_anonymous_says_none_and_tls_off(cfg):
    ctx = _run(_Store(), "_setup_custom_broker", CUSTOM_ANON)
    text = _text(ctx, "Custom Broker Saved")
    assert "Account: anonymous" in text
    assert "Password: none" in text
    assert "TLS: off" in text


# --- #2 an existing name is replaced only when the operator says so ---------

def _existing_custom():
    return create_custom_profile(name="mine", host="old.example", port=1883,
                                 username="carol", password="oldpw")


def test_r6_2_custom_existing_name_declined_writes_nothing(cfg):
    store = _Store({"mine": _existing_custom()})
    ctx = _run(store, "_setup_custom_broker", CUSTOM_TLS, yes=[False])
    assert _calls(ctx, "yesno", "Replace Profile?")
    q = _calls(ctx, "yesno", "Replace Profile?")[0][1][1]
    assert "old.example" in q and "oldpw" not in q
    assert not store.saves
    assert not (cfg / ms.FILENAME).exists()


def test_r6_2_custom_existing_name_accepted_says_replaced(cfg):
    store = _Store({"mine": _existing_custom()})
    ctx = _run(store, "_setup_custom_broker", CUSTOM_TLS, yes=[True])
    assert store.profiles["mine"].host == "mqtt.b.example"
    text = _text(ctx, "Custom Broker Saved")
    assert "replaced" in text and "created" not in text


def test_r6_2_custom_new_name_is_not_asked_and_says_created(cfg):
    ctx = _run(_Store(), "_setup_custom_broker", CUSTOM_TLS)
    assert not _calls(ctx, "yesno", "Replace Profile?")
    assert "created" in _text(ctx, "Custom Broker Saved")


PRIVATE = ["LongFast", "US", "meshforge", "newpw123"]


def test_r6_2_private_existing_declined_writes_nothing(cfg):
    store = _Store({"meshforge_private": create_private_profile(password="oldpw")})
    ctx = _run(store, "_setup_private_broker", PRIVATE, yes=[False])
    q = _calls(ctx, "yesno", "Replace Profile?")[0][1][1]
    assert "password" in q.lower() and "oldpw" not in q
    assert not store.saves
    assert not (cfg / ms.FILENAME).exists()


def test_r6_2_private_existing_accepted_says_replaced(cfg):
    store = _Store({"meshforge_private": create_private_profile(password="oldpw")})
    ctx = _run(store, "_setup_private_broker", PRIVATE, yes=[True])
    assert store.profiles["meshforge_private"].password == "newpw123"
    assert "replaced" in _text(ctx, "Setup Complete")


def test_r6_2_public_existing_says_replaced(cfg):
    store = _Store({"meshtastic_public": create_public_profile()})
    ctx = _run(store, "_setup_public_broker", ["HI", "LongFast"])
    assert "replaced" in _text(ctx, "Public Broker Set")


# --- #3 a password is never rendered unless the operator asks ---------------

def test_r6_3_commands_mask_the_password_by_default():
    p = create_custom_profile(name="r", host="mqtt.example.net", port=8883,
                              username="n", password="clubpw", use_tls=True)
    shown = get_meshtastic_mqtt_setup_commands(p)
    assert "clubpw" not in shown and PLACEHOLDER in shown
    assert "clubpw" in get_meshtastic_mqtt_setup_commands(p, reveal_password=True)


def test_r6_3_the_apply_argv_still_carries_the_password():
    """The in-app apply reads the argv SSOT, never the display."""
    p = create_private_profile(username="u", password="clubpw")
    assert "clubpw" in get_meshtastic_mqtt_setup_argv(p, host="h")[0]


def test_r6_3_commands_without_a_password_show_no_placeholder():
    p = create_custom_profile(name="r", host="mqtt.example.net", port=1883)
    assert PLACEHOLDER not in get_meshtastic_mqtt_setup_commands(p)


def _active_club():
    p = create_custom_profile(name="club", host="mqtt.example.net", port=1883,
                              username="n", password="clubpw")
    p.is_active = True
    return p


@pytest.mark.parametrize("answer", [False, None])
def test_r6_3_radio_setup_hides_the_stored_password_unless_asked(cfg, answer):
    store = _Store({"club": _active_club()})
    with patch("handlers.broker.load_profiles", side_effect=store.load):
        h, ctx = _broker()
        ctx.dialog._yesno_returns = [answer]
        h._radio_mqtt_setup()
    assert _calls(ctx, "yesno", "Show Password?")
    text = _text(ctx, "Radio MQTT Setup")
    assert "clubpw" not in text and PLACEHOLDER in text


def test_r6_3_radio_setup_reveals_only_on_explicit_yes(cfg):
    store = _Store({"club": _active_club()})
    with patch("handlers.broker.load_profiles", side_effect=store.load):
        h, ctx = _broker()
        ctx.dialog._yesno_returns = [True]
        h._radio_mqtt_setup()
    assert "clubpw" in _text(ctx, "Radio MQTT Setup")


def test_r6_3_radio_setup_without_a_password_does_not_ask(cfg):
    p = create_public_profile()
    p.password = ""
    p.is_active = True
    with patch("handlers.broker.load_profiles", return_value={"pub": p}):
        h, ctx = _broker()
        h._radio_mqtt_setup()
    assert not _calls(ctx, "yesno", "Show Password?")


def test_r6_3_private_setup_complete_never_shows_the_password(cfg):
    ctx = _run(_Store(), "_setup_private_broker", PRIVATE)
    text = _text(ctx, "Setup Complete")
    assert "newpw123" not in text and PLACEHOLDER in text
    assert "Password: set" in text


# --- #4 Esc abandons; BLANK takes the value shown ---------------------------

def test_r6_4_public_blank_takes_the_shown_defaults(cfg):
    store = _Store()
    _run(store, "_setup_public_broker", ["", ""])
    (p,) = store.profiles.values()
    assert (p.region, p.channel) == ("US", "LongFast")


def test_r6_4_private_blank_takes_the_shown_defaults(cfg):
    store = _Store()
    ctx = _run(store, "_setup_private_broker", ["", "", "", ""])
    p = store.profiles["meshforge_private"]
    (pw_box,) = _calls(ctx, "inputbox", "MQTT Password")
    assert (p.channel, p.region, p.username) == ("LongFast", "US", "meshforge")
    assert p.password and p.password == pw_box[2]["init"]


@pytest.mark.parametrize("flow,inputs", [
    ("_setup_public_broker", [None]),
    ("_setup_public_broker", ["US", None]),
    ("_setup_private_broker", [None]),
    ("_setup_private_broker", ["LongFast", None]),
    ("_setup_private_broker", ["LongFast", "US", None]),
    ("_setup_private_broker", ["LongFast", "US", "meshforge", None]),
])
def test_r6_4_esc_still_abandons(cfg, flow, inputs):
    store = _Store()
    _run(store, flow, inputs, yes=[True])
    assert not store.saves
    assert not (cfg / ms.FILENAME).exists()


def test_r6_4_whitespace_counts_as_blank(cfg):
    """The backend strips (backend._run strip=True); the fake does not, so
    the flow itself must not keep '   ' as a region."""
    store = _Store()
    _run(store, "_setup_public_broker", ["   ", "  "])
    (p,) = store.profiles.values()
    assert (p.region, p.channel) == ("US", "LongFast")
