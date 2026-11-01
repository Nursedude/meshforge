"""MQTT-CFG S1 — the one owner of `mqtt_nodeless.json` (`monitoring.mqtt_settings`).

Plan: `.claude/plans/mqtt_cfg_plan_2026_10_31.md` (r2). The file had several
writers and two schemas, so every reader carried a translator; three
non-author review rounds each found ~10 cases in those readers. The cure is
at the WRITE boundary: save() validates and stores INTENT, load() has three
states, and subscriptions() derives the topics from the subscriber dict.

Every test passes its own tmp config dir — the module never resolves a home.
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from monitoring import mqtt_settings as ms  # noqa: E402
from utils.paths import atomic_write_text_preserving  # noqa: E402

PRIVATE = {
    "broker": "mqtt.example.net", "port": 1883, "tls": "auto",
    "username": "op", "password": "pw",
    "root_topic": "msh/HI/2/e", "channel": "Regional", "key": "AQ==",
    "auto_start": True, "auto_start_telemetry": False,
}


def _file(d):
    return d / ms.FILENAME


def _plant(d, obj, mode=0o644):
    p = _file(d)
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj))
    os.chmod(p, mode)
    return p


def _save(d, intent):
    return ms.save(d, intent, atomic_write_text_preserving)


# --- validate: refuse what the author cannot have meant (hfm #3) ----------

def test_a_complete_intent_validates_clean():
    assert ms.validate(PRIVATE) == []


@pytest.mark.parametrize("field,value", [
    ("port", 0), ("port", 65536), ("port", "1883"), ("port", True),
    ("broker", ""), ("broker", None), ("broker", "a b"),
    ("channel", ""), ("channel", "Long/Fast"), ("channel", "#"),
    ("key", ""),
    ("tls", "yes"), ("tls", 1),
    ("username", None), ("password", 5),
    ("auto_start", "true"), ("auto_start_telemetry", 0),
])
def test_validate_names_the_bad_field(field, value):
    errs = ms.validate({**PRIVATE, field: value})
    assert errs and all(field in e for e in errs), errs


@pytest.mark.parametrize("root", ["msh", "msh/US", "msh/HI", "e", "/e", "msh/+/2/e", "msh/US/2/json"])
def test_root_topic_must_end_in_e(root):
    # the json leg is root.rsplit('/',1)[0] + "/json/": only an `/e` root
    # gives a real subscription (review F9 — the form's own help offered `msh`)
    errs = ms.validate({**PRIVATE, "root_topic": root})
    assert errs and "root_topic" in errs[0]


def test_single_level_wildcard_channel_is_allowed():
    # the local preset with no detected channel listens on every channel
    assert ms.validate({**PRIVATE, "channel": "+"}) == []


def test_unknown_key_is_refused():
    errs = ms.validate({**PRIVATE, "topic": "msh/US/2/e/LongFast/#"})
    assert errs and "topic" in errs[0]


def test_missing_required_field_is_refused_and_optional_ones_are_not():
    assert any("broker" in e for e in ms.validate({k: v for k, v in PRIVATE.items() if k != "broker"}))
    lean = {k: v for k, v in PRIVATE.items()
            if k not in ("username", "password", "auto_start", "auto_start_telemetry")}
    assert ms.validate(lean) == []


def test_non_dict_is_refused():
    assert ms.validate(None) and ms.validate([PRIVATE])


# --- load: ok / absent / unreadable, never a default on error (hfm #1) ----

def test_absent_is_absent_not_defaults(tmp_path):
    r = ms.load(tmp_path)
    assert (r.state, r.intent, r.error) == ("absent", None, None)


def test_round_trip_is_ok(tmp_path):
    assert _save(tmp_path, PRIVATE).ok
    r = ms.load(tmp_path)
    assert r.state == "ok" and r.intent == PRIVATE and r.legacy is False


@pytest.mark.parametrize("body", ["{not json", "[]", "null", '"x"'])
def test_garbage_is_unreadable(tmp_path, body):
    _plant(tmp_path, body)
    r = ms.load(tmp_path)
    assert r.state == "unreadable" and r.intent is None and r.error


def test_v1_with_a_bad_field_is_unreadable_naming_it(tmp_path):
    _plant(tmp_path, {"schema_version": 1, **PRIVATE, "port": "abc"})
    r = ms.load(tmp_path)
    assert r.state == "unreadable" and "port" in r.error


def test_v1_with_an_unknown_key_is_unreadable(tmp_path):
    _plant(tmp_path, {"schema_version": 1, **PRIVATE, "regions": ["US"]})
    r = ms.load(tmp_path)
    assert r.state == "unreadable" and "regions" in r.error


def test_newer_schema_is_refused_loudly(tmp_path):
    _plant(tmp_path, {"schema_version": 2, **PRIVATE})
    r = ms.load(tmp_path)
    assert r.state == "unreadable" and "newer" in r.error


@pytest.mark.parametrize("sv", [0, True, "1", None])
def test_nonsense_schema_version_is_unreadable(tmp_path, sv):
    _plant(tmp_path, {"schema_version": sv, **PRIVATE})
    assert ms.load(tmp_path).state == "unreadable"


def test_unreadable_parent_stays_inside_the_contract(tmp_path, monkeypatch):
    def boom(self):
        raise PermissionError("denied")
    monkeypatch.setattr(type(tmp_path), "exists", boom)
    r = ms.load(tmp_path)
    assert r.state == "unreadable" and "PermissionError" in r.error


def test_load_fills_the_optional_fields(tmp_path):
    lean = {k: v for k, v in PRIVATE.items()
            if k not in ("username", "password", "auto_start", "auto_start_telemetry")}
    _plant(tmp_path, {"schema_version": 1, **lean})
    r = ms.load(tmp_path)
    assert r.state == "ok"
    assert (r.intent["username"], r.intent["password"]) == ("", "")
    assert (r.intent["auto_start"], r.intent["auto_start_telemetry"]) == (False, True)


# --- save: refuse at the boundary, 0600, owner kept, .pre-v1 once ---------

def test_save_refuses_and_writes_nothing(tmp_path):
    res = _save(tmp_path, {**PRIVATE, "port": 70000})
    assert not res.ok and any("port" in e for e in res.errors)
    assert not _file(tmp_path).exists()


def test_refused_save_leaves_an_existing_file_byte_identical(tmp_path):
    assert _save(tmp_path, PRIVATE).ok
    before = _file(tmp_path).read_bytes()
    assert not _save(tmp_path, {**PRIVATE, "broker": ""}).ok
    assert _file(tmp_path).read_bytes() == before


def test_saved_file_is_versioned_intent_only(tmp_path):
    assert _save(tmp_path, PRIVATE).ok
    data = json.loads(_file(tmp_path).read_text())
    assert data.pop("schema_version") == 1
    assert data == PRIVATE  # no derived `topic`, delays, use_tls


def test_new_file_is_0600(tmp_path):
    assert _save(tmp_path, PRIVATE).ok
    assert stat.S_IMODE(_file(tmp_path).stat().st_mode) == 0o600


def test_a_world_readable_file_ends_0600(tmp_path):
    # atomic_write_text_preserving keeps the OLD mode; Q12 needs 0600 anyway
    _plant(tmp_path, {"schema_version": 1, **PRIVATE}, mode=0o644)
    assert _save(tmp_path, {**PRIVATE, "channel": "Other"}).ok
    assert stat.S_IMODE(_file(tmp_path).stat().st_mode) == 0o600


# --- review F1 (MA S5 pass, 2026-11-01): a ROOT-OWNED legacy file ----------
# The old TUI (under sudo) wrote `root 644`; the owner-preserving writer keeps
# root and save() forces 0600 -> operator-uid readers locked out, the
# subscriber reads `unreadable` and refuses, because Save was pressed. As root,
# save() hands the file to the CONFIG DIR's owner (the operator's own dir).

def _as_root(monkeypatch, owners):
    """euid 0; `owners` maps a path's name to its (uid, gid); chown recorded."""
    calls = []
    monkeypatch.setattr(ms.os, "geteuid", lambda: 0)
    monkeypatch.setattr(ms, "_owner", lambda p: owners.get(Path(p).name, (1000, 1000)), raising=False)
    monkeypatch.setattr(ms.os, "chown", lambda p, u, g: calls.append((Path(p).name, u, g)))
    return calls


def _plain_writer(path, text):
    Path(path).write_text(text)


def test_F1_root_owned_file_is_handed_to_the_config_dirs_owner(tmp_path, monkeypatch):
    _plant(tmp_path, {"schema_version": 1, **PRIVATE})
    calls = _as_root(monkeypatch, {ms.FILENAME: (0, 0), tmp_path.name: (1000, 1000)})
    assert ms.save(tmp_path, PRIVATE, _plain_writer).ok
    assert (ms.FILENAME, 1000, 1000) in calls


def test_F1_a_failed_handback_is_not_reported_as_saved(tmp_path, monkeypatch):
    _plant(tmp_path, {"schema_version": 1, **PRIVATE})
    _as_root(monkeypatch, {ms.FILENAME: (0, 0), tmp_path.name: (1000, 1000)})

    def refuse(p, u, g):
        raise PermissionError("EPERM")
    monkeypatch.setattr(ms.os, "chown", refuse)
    res = ms.save(tmp_path, PRIVATE, _plain_writer)
    assert not res.ok and "owner" in res.error and "1000" in res.error


def test_F1_a_root_owned_config_dir_is_not_a_handback_target(tmp_path, monkeypatch):
    _plant(tmp_path, {"schema_version": 1, **PRIVATE})
    calls = _as_root(monkeypatch, {ms.FILENAME: (0, 0), tmp_path.name: (0, 0)})
    assert ms.save(tmp_path, PRIVATE, _plain_writer).ok
    assert calls == []


def test_F1_not_root_never_chowns(tmp_path, monkeypatch):
    _plant(tmp_path, {"schema_version": 1, **PRIVATE})
    calls = []
    monkeypatch.setattr(ms.os, "chown", lambda *a: calls.append(a))
    assert _save(tmp_path, PRIVATE).ok
    assert calls == []


def test_save_goes_through_the_injected_writer(tmp_path):
    # the owner-preserving writer is the repo's (review F1: a root TUI must
    # not leave a root-owned file in the operator's home) — never a copy
    calls = []

    def writer(path, text):
        calls.append(path)
        atomic_write_text_preserving(path, text)

    assert ms.save(tmp_path, PRIVATE, writer).ok
    assert calls == [_file(tmp_path)]


def test_writer_failure_is_a_result_not_a_raise(tmp_path):
    def writer(path, text):
        raise OSError(28, "No space left on device")
    res = ms.save(tmp_path, PRIVATE, writer)
    assert not res.ok and "No space" in res.error


def test_first_save_over_legacy_keeps_pre_v1_once(tmp_path):
    legacy = {"broker": "localhost", "port": 1883, "topic": "msh/2/json/LongFast/#",
              "username": None, "password": None, "use_tls": False}
    raw = _plant(tmp_path, legacy).read_bytes()
    assert _save(tmp_path, PRIVATE).ok
    pre = tmp_path / (ms.FILENAME + ".pre-v1")
    assert pre.read_bytes() == raw
    assert stat.S_IMODE(pre.stat().st_mode) == 0o600
    # a second legacy-shaped file later never overwrites the first backup
    _plant(tmp_path, {**legacy, "broker": "other"})
    assert _save(tmp_path, PRIVATE).ok
    assert pre.read_bytes() == raw


def test_save_over_v1_makes_no_backup(tmp_path):
    assert _save(tmp_path, PRIVATE).ok
    assert _save(tmp_path, {**PRIVATE, "channel": "Other"}).ok
    assert not (tmp_path / (ms.FILENAME + ".pre-v1")).exists()


def test_save_refuses_to_downgrade_a_newer_file(tmp_path):
    raw = _plant(tmp_path, {"schema_version": 2, **PRIVATE}).read_bytes()
    res = _save(tmp_path, PRIVATE)
    assert not res.ok and "newer" in res.error
    assert _file(tmp_path).read_bytes() == raw


def test_save_creates_a_missing_config_dir(tmp_path):
    d = tmp_path / "fresh"
    assert _save(d, PRIVATE).ok and _file(d).exists()


# --- derivation: tls, subscriber dict, subscriptions ----------------------

@pytest.mark.parametrize("tls,port,want", [
    ("auto", 8883, True), ("auto", 1883, False), (True, 1883, True), (False, 8883, False),
])
def test_effective_tls(tls, port, want):
    assert ms.effective_tls({**PRIVATE, "tls": tls, "port": port}) is want


def test_subscriber_config_remote():
    cfg = ms.subscriber_config(PRIVATE)
    assert cfg == {
        "broker": "mqtt.example.net", "port": 1883, "username": "op", "password": "pw",
        "root_topic": "msh/HI/2/e", "channel": "Regional", "json_channel": "Regional",
        "key": "AQ==", "use_tls": False, "auto_reconnect": True,
        "reconnect_delay": 5, "max_reconnect_delay": 60,
    }


@pytest.mark.parametrize("broker", ["localhost", "127.0.0.1"])
def test_subscriber_config_local_listens_wide_and_reconnects_fast(broker):
    cfg = ms.subscriber_config({**PRIVATE, "broker": broker})
    assert (cfg["json_channel"], cfg["reconnect_delay"], cfg["max_reconnect_delay"]) == ("+", 2, 30)


@pytest.mark.parametrize("cfg,want", [
    ({"root_topic": "msh/US/2/e", "channel": "LongFast"},
     ["msh/US/2/json/LongFast/#", "msh/US/2/e/LongFast/#"]),
    ({"root_topic": "msh/2/e", "channel": "LongFast", "json_channel": "+"},
     ["msh/2/json/+/#", "msh/2/e/LongFast/#"]),
    # kilo's --root-topic/--channel overrides land on the subscriber dict (F5)
    ({"root_topic": "msh/HI/2/e", "channel": "Regional", "broker": "x"},
     ["msh/HI/2/json/Regional/#", "msh/HI/2/e/Regional/#"]),
    ({}, ["msh/US/2/json/LongFast/#", "msh/US/2/e/LongFast/#"]),
])
def test_subscriptions_from_the_subscriber_dict(cfg, want):
    assert ms.subscriptions(cfg) == want


def test_subscriptions_match_the_live_subscriber():
    # until S2 points _subscribe_topics at subscriptions(), pin them equal
    from monitoring.mqtt_subscriber import MQTTNodelessSubscriber

    class _Client:
        def __init__(self):
            self.topics = []

        def subscribe(self, t):
            self.topics.append(t)

    for cfg in (ms.subscriber_config(PRIVATE),
                ms.subscriber_config({**PRIVATE, "broker": "localhost"})):
        sub = MQTTNodelessSubscriber(config=dict(cfg))
        sub._client = _Client()
        sub._subscribe_topics()
        assert sub._client.topics == ms.subscriptions(cfg)


def test_defaults_are_the_public_broker_and_validate():
    from utils import mqtt_defaults as d
    i = ms.defaults()
    assert ms.validate(i) == []
    # one constant, two consumers (hfm #5): the stdlib module can't import them
    assert (i["broker"], i["port"], i["root_topic"], i["channel"], i["key"]) == (
        d.MESHTASTIC_PUBLIC_BROKER, d.MESHTASTIC_PUBLIC_PORT,
        d.MESHTASTIC_PUBLIC_ROOT_TOPIC, d.MESHTASTIC_PUBLIC_CHANNEL, d.MESHTASTIC_PUBLIC_KEY)
    assert (i["username"], i["password"], i["tls"]) == ("", "", "auto")


def test_defaults_derive_the_bare_constructors_subscription():
    # absent -> defaults() must subscribe exactly where today's bare
    # constructor does (F6: "absent" keeps today's behaviour)
    # (conftest redirects the subscriber's home, so its file is absent here)
    from monitoring.mqtt_subscriber import MQTTNodelessSubscriber

    class _Client:
        def __init__(self):
            self.topics = []

        def subscribe(self, t):
            self.topics.append(t)

    today = MQTTNodelessSubscriber()
    today._client = _Client()
    today._subscribe_topics()
    cfg = ms.subscriber_config(ms.defaults())
    assert today._client.topics == ms.subscriptions(cfg)
    for k in ("broker", "port", "username", "password", "use_tls",
              "reconnect_delay", "max_reconnect_delay"):
        assert cfg[k] == today._config[k], k


# --- from_legacy: Q13, the 6 known shapes, never guess --------------------

LEGACY = {
    "a_local_preset": (
        {"broker": "localhost", "port": 1883, "topic": "msh/2/json/LongFast/#",
         "username": None, "password": None, "use_tls": False},
        {"broker": "localhost", "port": 1883, "tls": "auto", "root_topic": "msh/2/e",
         "channel": "LongFast", "username": "", "password": ""}),
    "a_local_preset_no_channel": (
        {"broker": "localhost", "port": 1883, "topic": "msh/2/json/+/#",
         "username": None, "password": None, "use_tls": False},
        {"root_topic": "msh/2/e", "channel": "+"}),
    "b_public_preset": (
        {"broker": "mqtt.meshtastic.org", "port": 8883, "topic": "msh/US/2/e/LongFast/#",
         "username": "meshdev", "password": "large4cats", "use_tls": True},
        {"tls": "auto", "root_topic": "msh/US/2/e", "channel": "LongFast",
         "username": "meshdev", "password": "large4cats"}),
    "c_configure_topic_edit_over_stale_pair": (
        {"broker": "b.example", "port": 1883, "topic": "msh/US/2/e/NewChan/#",
         "root_topic": "msh/US/2/e", "channel": "OldChan", "use_tls": False,
         "auto_start": True, "auto_start_telemetry": False, "telemetry_poll_minutes": 30},
        {"root_topic": "msh/US/2/e", "channel": "NewChan", "auto_start": True,
         "auto_start_telemetry": False}),
    "d_private_pair": (
        {"broker": "b.example", "port": 8883, "topic": "msh/HI/2/e/Regional/#",
         "root_topic": "msh/HI/2/e", "channel": "Regional", "username": "u",
         "password": "p", "use_tls": True, "auto_start": False, "auto_start_telemetry": True},
        {"tls": "auto", "root_topic": "msh/HI/2/e", "channel": "Regional"}),
    "e_subscriber_schema": (
        {"broker": "b.example", "port": 8884, "username": "", "password": "",
         "root_topic": "msh/US/2/e", "channel": "LongFast", "key": "Zm9v",
         "use_tls": True, "regions": ["US"], "json_channel": "+", "auto_reconnect": True,
         "reconnect_delay": 5, "max_reconnect_delay": 60},
        {"tls": True, "key": "Zm9v", "root_topic": "msh/US/2/e", "channel": "LongFast"}),
    "f_broker_profile_json": (
        {"broker": "127.0.0.1", "port": 1883, "topic": "msh/US/2/json/LongFast/#",
         "username": None, "password": None, "use_tls": False},
        {"root_topic": "msh/US/2/e", "channel": "LongFast"}),
    "f_broker_profile_tls_off_8883": (
        {"broker": "b.example", "port": 8883, "topic": "msh/US/2/e/LongFast/#",
         "username": None, "password": None, "use_tls": False},
        {"tls": False}),
}


@pytest.mark.parametrize("name", sorted(LEGACY))
def test_legacy_shape_converts(name):
    raw, want = LEGACY[name]
    intent, err = ms.from_legacy(raw)
    assert err is None, err
    assert ms.validate(intent) == []
    for k, v in want.items():
        assert intent[k] == v, (k, intent[k], v)


def test_legacy_key_defaults_to_the_public_key():
    intent, _ = ms.from_legacy(LEGACY["b_public_preset"][0])
    assert intent["key"] == "AQ=="


def test_legacy_digit_string_port_converts():
    raw = {**LEGACY["b_public_preset"][0], "port": "8883"}
    intent, err = ms.from_legacy(raw)
    assert err is None and intent["port"] == 8883


@pytest.mark.parametrize("raw,field", [
    ({}, "broker"),                                                   # empty is not absent
    ({"broker": "b", "port": 8883, "topic": "msh/#"}, "topic"),       # too short
    ({"broker": "b", "port": 8883, "topic": "msh/US/2/e/#"}, "topic"),  # no channel segment
    ({"broker": "b", "port": 8883}, "topic"),                         # nothing to subscribe to
    ({"broker": "b", "port": 8883, "root_topic": "msh/US/2/e"}, "channel"),
    ({"broker": "b", "port": "abc", "topic": "msh/US/2/e/LongFast/#"}, "port"),
    ({"broker": "b", "port": 8883, "topic": "msh/US/2/e/LongFast/#", "use_tls": "y"}, "use_tls"),
    ({"broker": "b", "port": 8883, "topic": "msh/US/2/e/LongFast/#", "mystery": 1}, "mystery"),
    ({"broker": None, "port": 8883, "topic": "msh/US/2/e/LongFast/#"}, "broker"),
    ({"broker": "b", "port": 8883, "topic": "msh/US/2/x/LongFast/#"}, "root_topic"),
])
def test_ambiguous_legacy_is_unreadable_naming_the_field(raw, field):
    intent, err = ms.from_legacy(raw)
    assert intent is None and field in err, err


def test_a_consistent_pair_with_a_short_root_blames_the_root():
    # the private form's own help offered `msh/HI`: topic msh/HI/Ops/# agrees
    # with the pair, so the pair is the intent and the ROOT is what is wrong
    intent, err = ms.from_legacy({"broker": "b", "port": 1883, "topic": "msh/HI/Ops/#",
                                  "root_topic": "msh/HI", "channel": "Ops"})
    assert intent is None and err.startswith("root_topic"), err


def test_load_reports_a_legacy_file_and_writes_nothing(tmp_path):
    p = _plant(tmp_path, LEGACY["b_public_preset"][0])
    before = p.read_bytes()
    r = ms.load(tmp_path)
    assert r.state == "ok" and r.legacy is True
    assert p.read_bytes() == before
    assert not (tmp_path / (ms.FILENAME + ".pre-v1")).exists()


def test_load_of_ambiguous_legacy_is_unreadable(tmp_path):
    _plant(tmp_path, {"broker": "b", "port": 8883, "topic": "msh/#"})
    r = ms.load(tmp_path)
    assert r.state == "unreadable" and "topic" in r.error


def test_module_is_stdlib_only():
    # Q9/Q11: byte-identical in MeshForge and MeshAnchor — no first-party import
    import ast
    tree = ast.parse(open(ms.__file__, encoding="utf-8").read())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            assert n.level == 0, "relative import"
            mods.add(n.module.split(".")[0])
    assert mods <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names)


# --- review of f09fd86d..47d236f8 (2026-11-01) --------------------------------

def test_F1_save_over_an_unreadable_v1_file_keeps_its_bytes(tmp_path):
    # a typo in a hand-edited v1 file + one Save used to lose the broker,
    # credentials and key for good (backup only for NON-v1 files)
    raw = _plant(tmp_path, {"schema_version": 1, **PRIVATE, "channel": "Long Fast"}).read_bytes()
    assert ms.load(tmp_path).state == "unreadable"
    assert _save(tmp_path, ms.defaults()).ok
    backups = sorted(tmp_path.glob(ms.FILENAME + ".unreadable.*"))
    assert [b.read_bytes() for b in backups] == [raw]
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600


def test_F1_each_unreadable_save_keeps_its_own_backup(tmp_path):
    _plant(tmp_path, "{one")
    assert _save(tmp_path, PRIVATE).ok
    _plant(tmp_path, "{two")
    assert _save(tmp_path, PRIVATE).ok
    got = sorted(b.read_text() for b in tmp_path.glob(ms.FILENAME + ".unreadable.*"))
    assert got == ["{one", "{two"]


def test_F8_credentials_are_never_written_at_the_old_mode(tmp_path):
    # the owner-preserving writer copies the OLD mode onto the new file
    # before the rename; the old file must already be 0600 by then
    _plant(tmp_path, {"schema_version": 1, **PRIVATE}, mode=0o644)
    seen = []

    def writer(path, text):
        if path.exists():
            seen.append((path.name, stat.S_IMODE(path.stat().st_mode)))
        atomic_write_text_preserving(path, text)

    assert ms.save(tmp_path, {**PRIVATE, "channel": "Other"}, writer).ok
    assert seen == [(ms.FILENAME, 0o600)]


def test_F2_a_radio_root_gets_the_fix_suggested():
    # a root the generic example text cannot contain (msh/US/2/e is the example)
    errs = ms.validate({**PRIVATE, "root_topic": "msh/EU_868"})
    assert errs and "msh/EU_868/2/e" in errs[0]
