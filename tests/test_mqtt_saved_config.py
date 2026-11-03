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
from monitoring import mqtt_settings as ms  # noqa: E402
from monitoring.mqtt_subscriber import MQTTNodelessSubscriber  # noqa: E402


def subscriber_config_from_saved(saved):
    """MQTT-CFG S2: the old one-step translator is now two owned steps —
    a pre-v1 file converts (from_legacy), then the subscriber dict derives.
    Kept as a test helper so the characterization below still reads as the
    TUI's previous behaviour; a refused file raises ValueError as before."""
    intent, err = ms.from_legacy(saved)
    if err:
        raise ValueError(err)
    return ms.subscriber_config(intent)


def load_saved_mqtt_config(path):
    """(intent, None) | (None, None) absent | (None, error) — over load()."""
    r = ms.load(path.parent)
    return (r.intent, r.error) if r.state != "absent" else (None, None)

# The TUI's inline translation produced exactly this for its default config.
TUI_DEFAULT_OUT = {
    "broker": "mqtt.meshtastic.org", "port": 8883,
    "username": "", "password": "",
    "root_topic": "msh/US/2/e", "channel": "LongFast", "key": "AQ==",
    "use_tls": True, "auto_reconnect": True, "json_channel": "LongFast",
    "reconnect_delay": 5, "max_reconnect_delay": 60,
}


# --- the translation (characterization of the TUI's previous behaviour) ----

def test_tui_default_topic_schema_unchanged():
    saved = {"broker": "mqtt.meshtastic.org", "port": 8883,
             "topic": "msh/US/2/e/LongFast/#", "username": None, "password": None}
    assert subscriber_config_from_saved(saved) == TUI_DEFAULT_OUT


def test_empty_saved_is_refused_not_defaulted():
    # FLIPPED by Q13 (MQTT-CFG S2): `{}` used to read as the defaults; an
    # empty object is not "absent", so it is refused naming the field
    with pytest.raises(ValueError, match="broker"):
        subscriber_config_from_saved({})


def test_json_topic_maps_to_the_encrypted_root():
    out = subscriber_config_from_saved({"broker": "b", "topic": "msh/US/2/json/MediumFast/#"})
    assert out["root_topic"] == "msh/US/2/e"
    assert out["channel"] == "MediumFast"


def test_short_topic_is_refused_not_defaulted():
    # FLIPPED by Q13 (MQTT-CFG S2): `msh/#` used to fall back to LongFast on
    # msh/US/2/e — a guess. Too short to name a root and channel = refused.
    with pytest.raises(ValueError, match="topic"):
        subscriber_config_from_saved({"broker": "b", "topic": "msh/#"})


def test_localhost_gets_local_delays_and_plaintext_on_1883():
    out = subscriber_config_from_saved({"broker": "localhost", "port": 1883,
                                        "topic": "msh/HI/2/e/LongFast/#"})
    assert (out["reconnect_delay"], out["max_reconnect_delay"]) == (2, 30)
    assert out["use_tls"] is False
    assert out["root_topic"] == "msh/HI/2/e"


def test_explicit_use_tls_wins():
    out = subscriber_config_from_saved({"broker": "b", "port": 8883, "use_tls": False,
                                        "topic": "msh/US/2/e/LongFast/#"})
    assert out["use_tls"] is False


def test_credentials_carry_through():
    out = subscriber_config_from_saved({"broker": "b", "username": "u", "password": "p",
                                        "topic": "msh/US/2/e/LongFast/#"})
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
    p.write_text(json.dumps({"broker": "b", "topic": "msh/US/2/e/LongFast/#"}))
    intent, err = load_saved_mqtt_config(p)
    assert err is None and (intent["root_topic"], intent["channel"]) == ("msh/US/2/e", "LongFast")


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
    cfg, st = _start(MQTTSubscriberService(saved_dir=p.parent))
    assert cfg["broker"] == "broker.example"
    assert cfg["root_topic"] == "msh/HI/2/e"
    assert cfg["username"] == "u"
    assert st["config_source"] == "saved"


def test_global_ini_broker_port_overlay_the_saved_settings(tmp_path, connect_ok):
    p = tmp_path / "mqtt_nodeless.json"
    p.write_text(json.dumps({"broker": "broker.example", "port": 8883,
                             "topic": "msh/HI/2/e/LongFast/#"}))
    svc = MQTTSubscriberService(saved_dir=p.parent,
                                overlay={"broker": "localhost", "port": 1883})
    cfg, st = _start(svc)
    assert (cfg["broker"], cfg["port"]) == ("localhost", 1883)
    assert cfg["use_tls"] is False          # recomputed for the overlaid port
    assert cfg["root_topic"] == "msh/HI/2/e"  # the rest stays the operator's
    assert st["config_source"] == "saved+override"


def test_overlaid_port_rederives_tls_over_a_saved_use_tls(tmp_path, connect_ok):
    # review of b8bcb7b5 F3: every TUI save writes use_tls, so keeping it
    # under an overlaid port meant TLS against plaintext :1883 (this test
    # used to pin exactly that broken combination)
    p = tmp_path / "mqtt_nodeless.json"
    p.write_text(json.dumps({"broker": "mqtt.meshtastic.org", "port": 8883,
                             "use_tls": True, "topic": "msh/US/2/e/LongFast/#"}))
    cfg, _ = _start(MQTTSubscriberService(saved_dir=p.parent,
                                          overlay={"broker": "localhost", "port": 1883}))
    assert (cfg["broker"], cfg["port"], cfg["use_tls"]) == ("localhost", 1883, False)


def test_overlaid_broker_rederives_the_reconnect_delays(tmp_path, connect_ok):
    # F4: delays belong to the FINAL broker, not the saved one
    p = tmp_path / "mqtt_nodeless.json"
    p.write_text(json.dumps({"broker": "mqtt.meshtastic.org", "port": 1883,
                             "use_tls": False, "topic": "msh/US/2/e/LongFast/#"}))
    cfg, _ = _start(MQTTSubscriberService(saved_dir=p.parent, overlay={"broker": "localhost"}))
    assert (cfg["reconnect_delay"], cfg["max_reconnect_delay"]) == (2, 30)


def test_saved_use_tls_kept_when_no_port_is_overlaid(tmp_path, connect_ok):
    p = tmp_path / "mqtt_nodeless.json"
    p.write_text(json.dumps({"broker": "b", "port": 8884, "use_tls": True,
                             "topic": "msh/US/2/e/LongFast/#"}))
    cfg, _ = _start(MQTTSubscriberService(saved_dir=p.parent, overlay={"broker": "c"}))
    assert cfg["use_tls"] is True


# --- review of b8bcb7b5: F5 both schemas in one file, F6 types, F7 ----------

def test_a_root_that_cannot_give_a_json_leg_is_refused():
    # FLIPPED by plan-review F9 (MQTT-CFG S2): `msh/HI` + Ops used to be
    # accepted and subscribed `msh/json/Ops/#` — a topic no radio publishes
    saved = {"broker": "b", "topic": "msh/HI/Ops/#", "root_topic": "msh/HI", "channel": "Ops"}
    with pytest.raises(ValueError, match="root_topic"):
        subscriber_config_from_saved(saved)


def test_null_topic_is_refused_loudly():
    with pytest.raises(ValueError, match="topic"):
        subscriber_config_from_saved({"broker": "b", "topic": None})


def test_non_string_broker_is_refused_loudly():
    with pytest.raises(ValueError, match="broker"):
        subscriber_config_from_saved({"broker": 5, "topic": "msh/US/2/e/LongFast/#"})


def test_digit_string_port_is_an_int_so_tls_is_right():
    out = subscriber_config_from_saved({"broker": "b", "port": "8883",
                                        "topic": "msh/US/2/e/LongFast/#"})
    assert out["port"] == 8883 and out["use_tls"] is True


def test_garbage_port_is_refused_loudly():
    with pytest.raises(ValueError, match="port"):
        subscriber_config_from_saved({"broker": "b", "port": "eighty",
                                      "topic": "msh/US/2/e/LongFast/#"})


def test_invalid_saved_types_read_as_unreadable_in_the_daemon(tmp_path, connect_ok):
    p = tmp_path / "mqtt_nodeless.json"
    p.write_text(json.dumps({"topic": None}))
    cfg, st = _start(MQTTSubscriberService(broker="localhost", port=1883, saved_dir=p.parent))
    assert st["config_source"] == "saved-unreadable"
    assert cfg["broker"] == "localhost"


def test_permission_error_on_exists_stays_in_the_contract(tmp_path):
    p = tmp_path / "mqtt_nodeless.json"
    with patch.object(type(p), "exists", side_effect=PermissionError("denied")):
        saved, err = load_saved_mqtt_config(p)
    assert saved is None and err and "PermissionError" in err


# --- F8: the subscriber's own loader reads through the same translation ---

def test_subscriber_default_construction_reads_a_tui_schema_file(tmp_path):
    p = tmp_path / "mqtt_nodeless.json"
    p.write_text(json.dumps({"broker": "b", "port": 1883,
                             "topic": "msh/HI/2/e/LongFast/#"}))
    with patch("monitoring.mqtt_subscriber.mqtt_config_dir", return_value=p.parent):
        s = MQTTNodelessSubscriber()
    assert s._config["root_topic"] == "msh/HI/2/e"


def test_subscriber_schema_extras_are_derived_not_stored(tmp_path):
    # FLIPPED by Q14 (MQTT-CFG S2): `regions` had no consumer and is dropped;
    # json_channel is DERIVED ("+" only for a local broker), not carried
    p = tmp_path / "mqtt_nodeless.json"
    p.write_text(json.dumps({"broker": "b", "root_topic": "msh/EU_868/2/e",
                             "channel": "LongFast", "regions": ["EU_868"],
                             "json_channel": "+"}))
    with patch("monitoring.mqtt_subscriber.mqtt_config_dir", return_value=p.parent):
        s = MQTTNodelessSubscriber()
    assert "regions" not in s._config
    assert s._config["json_channel"] == "LongFast"
    assert s.config_error is None


def test_unreadable_saved_file_makes_the_bare_subscriber_refuse(tmp_path):
    # MQTT-CFG F6: it used to start on the public defaults
    p = tmp_path / "mqtt_nodeless.json"
    p.write_text(json.dumps({"schema_version": 1, "broker": "b", "port": "abc"}))
    with patch("monitoring.mqtt_subscriber.mqtt_config_dir", return_value=tmp_path), \
         patch.object(MQTTNodelessSubscriber, "_connect", return_value=True) as c:
        s = MQTTNodelessSubscriber()
        assert s.start() is False
    assert "port" in s.config_error and not c.called and not s.is_running()


# --- F9: one path, used by the TUI's own load/save -------------------------

def _tui_save(tui_mqtt, intent):
    """The TUI write path under a fresh edit lease (F8 v2, V9) -> bool."""
    r = tui_mqtt.acquire_mqtt_edit_lease()
    assert r.state == "held", r
    with r.lease as lease:
        return tui_mqtt.save_mqtt_config(intent, lease).ok


def test_tui_load_and_save_use_the_shared_path(tmp_path):
    from launcher_tui.handlers import mqtt as tui_mqtt
    with patch("monitoring.mqtt_subscriber.mqtt_config_dir", return_value=tmp_path):
        assert _tui_save(tui_mqtt, {**ms.defaults(), "broker": "x"})
        assert tui_mqtt.load_mqtt_config()["broker"] == "x"
    assert (tmp_path / ms.FILENAME).exists()


# --- F2/F10: which layer set broker/port is recorded once, on DaemonConfig ---

def test_daemon_config_records_explicit_yaml_keys(tmp_path):
    from daemon_config import DaemonConfig
    y = tmp_path / "daemon.yaml"
    y.write_text("mqtt_broker: broker.lan\n")
    with patch("daemon_config.load_global_overrides", return_value={}):
        cfg = DaemonConfig.load(y)
    assert "mqtt_broker" in cfg.explicit_keys
    assert "mqtt_port" not in cfg.explicit_keys


def test_daemon_config_records_explicit_global_keys(tmp_path):
    from daemon_config import DaemonConfig
    with patch("daemon_config.load_global_overrides",
               return_value={"mqtt_broker": "g", "mqtt_port": 1884}):
        cfg = DaemonConfig.load(tmp_path / "absent.yaml")
    assert {"mqtt_broker", "mqtt_port"} <= cfg.explicit_keys


def test_mqtt_overlay_comes_from_explicit_keys_only(tmp_path):
    from daemon import _mqtt_overlay
    from daemon_config import DaemonConfig
    with patch("daemon_config.load_global_overrides", return_value={}):
        cfg = DaemonConfig.load(tmp_path / "absent.yaml")
    assert _mqtt_overlay(cfg) == {}          # defaults are not "explicit"
    cfg.mqtt_broker = "broker.lan"
    cfg.explicit_keys.add("mqtt_broker")
    assert _mqtt_overlay(cfg) == {"broker": "broker.lan"}


# --- F1: the daemon unit runs as root with no SUDO_USER — blind, so LOUD ----

def _blind_check(tmp_path, resolved_home, root_home="/root"):
    """Run the daemon's no-saved-file branch as root, with the RESOLVER's
    answer pinned — the decision must come from it, not a second rule."""
    import pwd as _pwd
    from pathlib import Path as _P
    svc = MQTTSubscriberService(broker="localhost", port=1883)
    fake_pw = type("pw", (), {"pw_dir": root_home})()
    with patch("os.geteuid", return_value=0), \
         patch("daemon.get_real_user_home", return_value=_P(resolved_home)), \
         patch.object(_pwd, "getpwuid", return_value=fake_pw), \
         patch("monitoring.mqtt_subscriber.mqtt_config_dir",
               return_value=tmp_path):
        _, st = _start(svc)
    return st["config_source"]


def test_root_whose_resolver_falls_back_to_root_home_is_blind(tmp_path, connect_ok, caplog):
    # no SUDO_USER/LOGNAME, or SUDO_USER=root: the resolver returns root's own
    # home — the operator's is unknowable, so "no file" is UNOBSERVED
    with caplog.at_level(logging.WARNING):
        assert _blind_check(tmp_path, "/root") == "operator-home-unknown"
    assert any("SUDO_USER" in r.getMessage() for r in caplog.records)


def test_root_whose_resolver_found_the_operator_is_not_blind(tmp_path, connect_ok):
    # review of d583e37e F1: LOGNAME=<operator> resolves the home; that home
    # simply has no saved file — a plain local-default, no false WARNING
    assert _blind_check(tmp_path, "/home/op") == "local-default"


# --- review of d583e37e F2: the TUI Configure menu edits ONLY `topic` --------

def test_topic_edited_after_a_private_save_wins_over_the_stale_pair():
    saved = {"broker": "b", "topic": "msh/US/2/e/LongFast/#",  # edited later
             "root_topic": "msh/HI", "channel": "Ops"}         # stale private save
    out = subscriber_config_from_saved(saved)
    assert (out["root_topic"], out["channel"]) == ("msh/US/2/e", "LongFast")


def test_consistent_private_save_with_an_e_root_converts():
    # the pair the TUI's private save writes together: topic == root/channel/#
    # (a SHORT root like `msh/HI` is now refused — see the F9 test above)
    saved = {"broker": "b", "topic": "msh/HI/2/e/Ops/#", "root_topic": "msh/HI/2/e", "channel": "Ops"}
    out = subscriber_config_from_saved(saved)
    assert (out["root_topic"], out["channel"]) == ("msh/HI/2/e", "Ops")


def test_no_saved_file_means_the_local_broker(tmp_path, connect_ok):
    svc = MQTTSubscriberService(broker="localhost", port=1883,
                                saved_dir=tmp_path)
    cfg, st = _start(svc)
    assert (cfg["broker"], cfg["port"], cfg["use_tls"]) == ("localhost", 1883, False)
    assert st["config_source"] == "local-default"


def test_corrupt_saved_file_is_loud_and_visible(tmp_path, connect_ok, caplog):
    p = tmp_path / "mqtt_nodeless.json"
    p.write_text("{broken")
    svc = MQTTSubscriberService(broker="localhost", port=1883, saved_dir=p.parent)
    with caplog.at_level(logging.WARNING):
        cfg, st = _start(svc)
    assert cfg["broker"] == "localhost"
    assert st["config_source"] == "saved-unreadable"
    assert any("mqtt_nodeless.json" in r.getMessage() and r.levelno >= logging.WARNING
               for r in caplog.records)


# --- the suite itself must never resolve the operator's REAL saved file -----

def test_suite_never_resolves_the_real_saved_settings_path():
    # 2026-10-31: a red-first run of this file against the old hardcoded
    # save_mqtt_config overwrote the manager box's real mqtt_nodeless.json. The
    # conftest _OPERATOR_STORE_MODULES redirect is the gate; this pins it.
    import pwd
    from launcher_tui.handlers import mqtt as tui_mqtt
    from monitoring import mqtt_subscriber
    real_home = pwd.getpwuid(os.getuid()).pw_dir
    assert not str(mqtt_subscriber.mqtt_config_dir()).startswith(real_home + "/")
    assert not str(tui_mqtt.get_real_user_home()).startswith(real_home)


# --- MQTT-CFG S3: the TUI edits INTENT (the S2 menu-shape adapter is gone) ---

def _tui(tmp_path):
    from launcher_tui.handlers import mqtt as tui_mqtt
    return tui_mqtt, patch("monitoring.mqtt_subscriber.mqtt_config_dir",
                           return_value=tmp_path)


def test_tui_load_then_save_is_a_fixed_point(tmp_path):
    tui_mqtt, p = _tui(tmp_path)
    with p:
        assert _tui_save(tui_mqtt, {**ms.defaults(), "broker": "b.example",
                                          "port": 1883, "channel": "Ops",
                                          "username": "u", "password": "p",
                                          "auto_start": True})
        first = (tmp_path / ms.FILENAME).read_bytes()
        assert _tui_save(tui_mqtt, tui_mqtt.load_mqtt_config())
    assert (tmp_path / ms.FILENAME).read_bytes() == first


def test_tui_reads_a_legacy_file_and_the_next_save_converts_it(tmp_path):
    (tmp_path / ms.FILENAME).write_text(json.dumps(
        {"broker": "b", "port": 1883, "topic": "msh/US/2/e/Old/#", "use_tls": False}))
    tui_mqtt, p = _tui(tmp_path)
    with p:
        cfg = tui_mqtt.load_mqtt_config()
        assert cfg["channel"] == "Old"
        assert _tui_save(tui_mqtt, {**cfg, "channel": "New"})
        assert tui_mqtt.load_mqtt_config()["channel"] == "New"
    assert json.loads((tmp_path / ms.FILENAME).read_text())["schema_version"] == 1
    assert (tmp_path / (ms.FILENAME + ".pre-v1")).exists()


def test_tui_refused_save_returns_false_and_keeps_the_file(tmp_path):
    tui_mqtt, p = _tui(tmp_path)
    with p:
        assert _tui_save(tui_mqtt, ms.defaults())
        before = (tmp_path / ms.FILENAME).read_bytes()
        assert _tui_save(tui_mqtt, {**ms.defaults(), "port": 70000}) is False
    assert (tmp_path / ms.FILENAME).read_bytes() == before


def test_tui_start_refuses_an_unreadable_file(tmp_path):
    (tmp_path / ms.FILENAME).write_text("{broken")
    tui_mqtt, p = _tui(tmp_path)
    with p:
        cfg, err = tui_mqtt._saved_subscriber_config()
        menus = tui_mqtt.load_mqtt_config()
    assert cfg is None and "JSON" in err
    assert menus["broker"] == "mqtt.meshtastic.org"   # menus still editable


def test_tui_absent_file_starts_on_the_public_defaults(tmp_path):
    tui_mqtt, p = _tui(tmp_path)
    with p:
        cfg, err = tui_mqtt._saved_subscriber_config()
    assert err is None and cfg["broker"] == "mqtt.meshtastic.org" and cfg["use_tls"] is True


def test_overlaid_port_resets_an_explicit_saved_tls(tmp_path, connect_ok):
    # b8bcb7b5 F3 in the intent model: the S2 conversion turns use_tls=True
    # on 8883 into "auto", so the case that still needs the re-derive is an
    # EXPLICIT tls (here on 8884) under an overlaid plaintext port
    (tmp_path / ms.FILENAME).write_text(json.dumps({
        "schema_version": 1, "broker": "b", "port": 8884, "tls": True,
        "root_topic": "msh/US/2/e", "channel": "LongFast", "key": "AQ=="}))
    cfg, _ = _start(MQTTSubscriberService(saved_dir=tmp_path, overlay={"port": 1883}))
    assert (cfg["port"], cfg["use_tls"]) == (1883, False)


def test_F3_an_invalid_override_falls_back_to_the_real_local_broker(tmp_path, connect_ok):
    # the daemon passes cfg.mqtt_broker as BOTH the fallback and the overlay,
    # so "fall back to the local broker" used the same invalid value
    (tmp_path / ms.FILENAME).write_text(json.dumps({**ms.defaults(), "schema_version": 1}))
    svc = MQTTSubscriberService(broker="bad host", port=1883, saved_dir=tmp_path,
                                overlay={"broker": "bad host"})
    cfg, st = _start(svc)
    assert st["config_source"] == "override-invalid"
    assert (cfg["broker"], cfg["port"], cfg["use_tls"]) == ("localhost", 1883, False)
