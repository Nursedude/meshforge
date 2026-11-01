"""One reader for the saved MQTT settings (`mqtt_nodeless.json`).

Review of a30390ae (F1/F2, 2026-10-31): the file is written in TWO schemas —
the TUI's `save_mqtt_config` writes `topic` ("msh/US/2/e/LongFast/#"), the
subscriber's own `save_config` writes `root_topic`/`channel`/`use_tls` — and
the TUI translated it inline in two duplicate copies while the daemon ignored
it entirely. `subscriber_config_from_saved` is now the ONE translation; the
first tests pin it to the TUI's previous output exactly (characterization),
so moving the TUI onto it changes nothing a TUI user can see.
"""
import json
import logging
import os
import sys
from unittest.mock import patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from daemon import MQTTSubscriberService  # noqa: E402
from monitoring.mqtt_subscriber import (  # noqa: E402
    MQTTNodelessSubscriber, load_saved_mqtt_config, subscriber_config_from_saved,
)

# The TUI's inline translation produced exactly this for its default config.
TUI_DEFAULT_OUT = {
    "broker": "mqtt.meshtastic.org", "port": 8883,
    "username": "", "password": "",
    "root_topic": "msh/US/2/e", "channel": "LongFast", "key": "AQ==",
    "use_tls": True, "auto_reconnect": True,
    "reconnect_delay": 5, "max_reconnect_delay": 60,
}


# --- the translation (characterization of the TUI's previous behaviour) ----

def test_tui_default_topic_schema_unchanged():
    saved = {"broker": "mqtt.meshtastic.org", "port": 8883,
             "topic": "msh/US/2/e/LongFast/#", "username": None, "password": None}
    assert subscriber_config_from_saved(saved) == TUI_DEFAULT_OUT


def test_empty_saved_uses_the_tui_defaults():
    assert subscriber_config_from_saved({}) == TUI_DEFAULT_OUT


def test_json_topic_maps_to_the_encrypted_root():
    out = subscriber_config_from_saved({"topic": "msh/US/2/json/MediumFast/#"})
    assert out["root_topic"] == "msh/US/2/e"
    assert out["channel"] == "MediumFast"


def test_short_topic_falls_back_like_the_tui():
    out = subscriber_config_from_saved({"topic": "msh/#"})
    assert out["root_topic"] == "msh/US/2/e"
    assert out["channel"] == "LongFast"


def test_localhost_gets_local_delays_and_plaintext_on_1883():
    out = subscriber_config_from_saved({"broker": "localhost", "port": 1883,
                                        "topic": "msh/HI/2/e/LongFast/#"})
    assert (out["reconnect_delay"], out["max_reconnect_delay"]) == (2, 30)
    assert out["use_tls"] is False
    assert out["root_topic"] == "msh/HI/2/e"


def test_explicit_use_tls_wins():
    out = subscriber_config_from_saved({"port": 8883, "use_tls": False})
    assert out["use_tls"] is False


def test_credentials_carry_through():
    out = subscriber_config_from_saved({"username": "u", "password": "p"})
    assert (out["username"], out["password"]) == ("u", "p")


# --- the OTHER schema: written by the subscriber's own save_config ---------

def test_subscriber_schema_is_read_not_replaced_by_defaults():
    # the TUI used to ignore root_topic here and fall back to msh/US/2/e
    saved = {"broker": "b", "port": 1883, "root_topic": "msh/EU_868/2/e",
             "channel": "MediumSlow", "use_tls": False, "key": "XYZ="}
    out = subscriber_config_from_saved(saved)
    assert out["root_topic"] == "msh/EU_868/2/e"
    assert out["channel"] == "MediumSlow"
    assert out["key"] == "XYZ="
    assert out["use_tls"] is False


# --- reading the file: absent / unreadable / ok are THREE states -----------

def test_load_absent_is_none_without_error(tmp_path):
    assert load_saved_mqtt_config(tmp_path / "nope.json") == (None, None)


def test_load_corrupt_is_an_error_not_defaults(tmp_path):
    p = tmp_path / "mqtt_nodeless.json"
    p.write_text("{not json")
    saved, err = load_saved_mqtt_config(p)
    assert saved is None
    assert err and "mqtt_nodeless.json" in err


def test_load_non_object_is_an_error(tmp_path):
    p = tmp_path / "mqtt_nodeless.json"
    p.write_text("[1, 2]")
    saved, err = load_saved_mqtt_config(p)
    assert saved is None and err


def test_load_ok(tmp_path):
    p = tmp_path / "mqtt_nodeless.json"
    p.write_text(json.dumps({"topic": "msh/US/2/e/LongFast/#"}))
    assert load_saved_mqtt_config(p) == ({"topic": "msh/US/2/e/LongFast/#"}, None)


# --- daemon precedence: saved -> global.ini broker/port -> local mosquitto --

@pytest.fixture
def connect_ok():
    with patch.object(MQTTNodelessSubscriber, "_connect", return_value=True):
        yield


def _start(svc):
    try:
        assert svc.start() is True
        return svc._subscriber._config, svc.get_status()
    finally:
        svc.stop()


def test_daemon_uses_the_saved_settings(tmp_path, connect_ok):
    p = tmp_path / "mqtt_nodeless.json"
    p.write_text(json.dumps({"broker": "broker.example", "port": 1883,
                             "topic": "msh/HI/2/e/LongFast/#", "username": "u"}))
    cfg, st = _start(MQTTSubscriberService(saved_path=p))
    assert cfg["broker"] == "broker.example"
    assert cfg["root_topic"] == "msh/HI/2/e"
    assert cfg["username"] == "u"
    assert st["config_source"] == "saved"


def test_global_ini_broker_port_overlay_the_saved_settings(tmp_path, connect_ok):
    p = tmp_path / "mqtt_nodeless.json"
    p.write_text(json.dumps({"broker": "broker.example", "port": 8883,
                             "topic": "msh/HI/2/e/LongFast/#"}))
    svc = MQTTSubscriberService(saved_path=p,
                                overlay={"broker": "localhost", "port": 1883})
    cfg, st = _start(svc)
    assert (cfg["broker"], cfg["port"]) == ("localhost", 1883)
    assert cfg["use_tls"] is False          # recomputed for the overlaid port
    assert cfg["root_topic"] == "msh/HI/2/e"  # the rest stays the operator's
    assert st["config_source"] == "saved+global.ini"


def test_overlay_keeps_an_explicit_saved_use_tls(tmp_path, connect_ok):
    p = tmp_path / "mqtt_nodeless.json"
    p.write_text(json.dumps({"broker": "b", "use_tls": True}))
    cfg, _ = _start(MQTTSubscriberService(saved_path=p, overlay={"port": 1883}))
    assert cfg["use_tls"] is True


def test_no_saved_file_means_the_local_broker(tmp_path, connect_ok):
    svc = MQTTSubscriberService(broker="localhost", port=1883,
                                saved_path=tmp_path / "absent.json")
    cfg, st = _start(svc)
    assert (cfg["broker"], cfg["port"], cfg["use_tls"]) == ("localhost", 1883, False)
    assert st["config_source"] == "local-default"


def test_corrupt_saved_file_is_loud_and_visible(tmp_path, connect_ok, caplog):
    p = tmp_path / "mqtt_nodeless.json"
    p.write_text("{broken")
    svc = MQTTSubscriberService(broker="localhost", port=1883, saved_path=p)
    with caplog.at_level(logging.WARNING):
        cfg, st = _start(svc)
    assert cfg["broker"] == "localhost"
    assert st["config_source"] == "saved-unreadable"
    assert any("mqtt_nodeless.json" in r.getMessage() and r.levelno >= logging.WARNING
               for r in caplog.records)
