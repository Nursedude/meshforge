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


def _held(d):
    """A fresh held lease — what every save needs since F8 v2 (V9)."""
    r = ms.acquire_lease(d, user="test", tty="-")
    assert r.state == "held", r
    return r.lease


def _save(d, intent, reset=False):
    return ms.save(d, intent, atomic_write_text_preserving, _held(d), reset=reset)


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
    assert ms.save(tmp_path, PRIVATE, _plain_writer, _held(tmp_path)).ok
    assert (ms.FILENAME, 1000, 1000) in calls


def test_F1_a_failed_handback_is_not_reported_as_saved(tmp_path, monkeypatch):
    _plant(tmp_path, {"schema_version": 1, **PRIVATE})
    _as_root(monkeypatch, {ms.FILENAME: (0, 0), tmp_path.name: (1000, 1000)})

    def refuse(p, u, g):
        raise PermissionError("EPERM")
    monkeypatch.setattr(ms.os, "chown", refuse)
    res = ms.save(tmp_path, PRIVATE, _plain_writer, _held(tmp_path))
    assert not res.ok and "owner" in res.error and "1000" in res.error


def test_F1_a_root_owned_config_dir_is_not_a_handback_target(tmp_path, monkeypatch):
    _plant(tmp_path, {"schema_version": 1, **PRIVATE})
    calls = _as_root(monkeypatch, {ms.FILENAME: (0, 0), tmp_path.name: (0, 0)})
    assert ms.save(tmp_path, PRIVATE, _plain_writer, _held(tmp_path)).ok
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

    assert ms.save(tmp_path, PRIVATE, writer, _held(tmp_path)).ok
    assert calls == [_file(tmp_path)]


def test_writer_failure_is_a_result_not_a_raise(tmp_path):
    def writer(path, text):
        raise OSError(28, "No space left on device")
    res = ms.save(tmp_path, PRIVATE, writer, _held(tmp_path))
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
    assert _save(tmp_path, ms.defaults()).code == "unreadable"   # Q15: asks first
    assert _save(tmp_path, ms.defaults(), reset=True).ok
    backups = sorted(tmp_path.glob(ms.FILENAME + ".unreadable.*"))
    assert [b.read_bytes() for b in backups] == [raw]
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600


def test_F1_each_unreadable_save_keeps_its_own_backup(tmp_path):
    _plant(tmp_path, "{one")
    assert _save(tmp_path, PRIVATE, reset=True).ok
    _plant(tmp_path, "{two")
    assert _save(tmp_path, PRIVATE, reset=True).ok
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

    assert ms.save(tmp_path, {**PRIVATE, "channel": "Other"}, writer, _held(tmp_path)).ok
    assert seen == [(ms.FILENAME, 0o600)]


def test_F2_a_radio_root_gets_the_fix_suggested():
    # a root the generic example text cannot contain (msh/US/2/e is the example)
    errs = ms.validate({**PRIVATE, "root_topic": "msh/EU_868"})
    assert errs and "msh/EU_868/2/e" in errs[0]


# --- F8 v2: the EDIT LEASE (grill 2026-11-03, V1-V14) ----------------------
#
# One MQTT editor at a time (V1). save() refuses without a held lease for
# THIS config dir (V9); a file that changed since the lease was taken was
# written by someone who skipped it, so Save refuses and writes nothing (V7)
# — except Reset over a file that BROKE (V2: rebuild from the screen). The
# saved password never leaves the file: a screen carries KEEP (Q3), and KEEP
# across an account change is refused (Q7, Q4 normalization, Q8).

W = atomic_write_text_preserving
SAVED = {**PRIVATE, "broker": "mqtt.a.example", "username": "alice",
         "password": "s3cret"}


def _lease(d, user="op", tty="pts/1"):
    r = ms.acquire_lease(d, user=user, tty=tty)
    assert r.state == "held", r
    return r.lease


def _seed(d, intent=SAVED):
    with _lease(d) as lease:
        res = ms.save(d, dict(intent), W, lease)
        assert res.ok, res
    return json.loads(_file(d).read_text())


def _disk(d):
    return json.loads(_file(d).read_text())


def test_v9_save_without_a_lease_is_refused_and_writes_nothing(tmp_path):
    before = _seed(tmp_path)
    res = ms.save(tmp_path, {**SAVED, "port": 1999}, W, None)
    assert not res.ok and res.code == "no_lease"
    assert _disk(tmp_path) == before


def test_v9_a_released_lease_no_longer_authorizes_a_save(tmp_path):
    before = _seed(tmp_path)
    lease = _lease(tmp_path)
    lease.release()
    res = ms.save(tmp_path, {**SAVED, "port": 1999}, W, lease)
    assert not res.ok and res.code == "no_lease"
    assert _disk(tmp_path) == before


def test_v9_a_lease_for_another_config_dir_is_refused(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    with _lease(b) as other:
        res = ms.save(a, dict(SAVED), W, other)
    assert not res.ok and res.code == "no_lease"
    assert not _file(a).exists()


def test_v1_v12_a_second_opener_is_busy_and_names_the_holder(tmp_path):
    with _lease(tmp_path, user="alice", tty="pts/3"):
        r = ms.acquire_lease(tmp_path, user="bob", tty="pts/4")
        assert r.state == "busy" and r.lease is None
        assert r.holder.user == "alice" and r.holder.tty == "pts/3"
        assert r.holder.pid == os.getpid() and r.holder.since


@pytest.mark.parametrize("record", ["", "not json", "{\"pid\": \"x\"}",
                                    json.dumps({"pid": 2 ** 22 + 7, "user": "ghost",
                                                "tty": "-", "since": "-"})])
def test_v12_an_unreadable_or_dead_holder_record_reads_unknown_never_free(tmp_path, record):
    with _lease(tmp_path) as lease:
        Path(lease.path).write_text(record)      # the record, not the lock
        r = ms.acquire_lease(tmp_path, user="bob", tty="pts/4")
        assert r.state == "busy" and r.lease is None
        assert r.holder is None                  # "held by an unknown session"


def test_v1_release_frees_the_lease_and_clears_the_record(tmp_path):
    lease = _lease(tmp_path)
    path = Path(lease.path)
    lease.release()
    assert path.read_text() == ""                # no stale holder left behind
    assert ms.acquire_lease(tmp_path, user="b", tty="t").state == "held"


def test_v1_the_lease_is_held_against_a_real_second_process(tmp_path):
    """Both review rounds could not verify a real second-process holder."""
    import subprocess
    script = tmp_path / "hold.py"
    script.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {os.path.abspath(SRC_DIR)!r})\n"
        "from pathlib import Path\n"
        "from monitoring import mqtt_settings as ms\n"
        "r = ms.acquire_lease(Path(sys.argv[1]), user='other', tty='pts/9')\n"
        "print(r.state, flush=True)\n"
        "sys.stdin.readline()\n")
    p = subprocess.Popen([sys.executable, "-I", str(script), str(tmp_path)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "held"
        r = ms.acquire_lease(tmp_path, user="me", tty="pts/1")
        assert r.state == "busy" and r.holder.user == "other"
        assert r.holder.pid == p.pid
    finally:
        p.communicate("\n", timeout=10)
    assert ms.acquire_lease(tmp_path, user="me", tty="pts/1").state == "held"


def test_v10_the_lease_file_sits_beside_the_config_dir_not_inside_it(tmp_path):
    d = tmp_path / "meshforge"                   # need not exist yet (r3 #4)
    p = ms.lease_path(d)
    assert p.parent == tmp_path and d.name in p.name
    assert ms.acquire_lease(d, user="op", tty="t").state == "held"


def test_v7_a_change_without_the_lease_refuses_and_keeps_the_other_write(tmp_path):
    _seed(tmp_path)
    with _lease(tmp_path) as lease:
        hand = {"schema_version": 1, **SAVED, "port": 1885}
        _file(tmp_path).write_text(json.dumps(hand))      # a lease-less writer
        res = ms.save(tmp_path, {**lease.view, "channel": "Mine"}, W, lease)
        assert not res.ok and res.code == "changed_without_lease"
        assert not res.now_unreadable
        again = ms.save(tmp_path, {**lease.view, "channel": "Mine"}, W, lease,
                        reset=True)
        assert not again.ok and again.code == "changed_without_lease"
    assert _disk(tmp_path)["port"] == 1885 and _disk(tmp_path)["channel"] == "Regional"


def test_v2_reset_over_a_file_that_broke_after_open_writes_the_screen(tmp_path):
    _seed(tmp_path)
    with _lease(tmp_path) as lease:
        _file(tmp_path).write_text("")                    # zero-byte power-loss
        screen = {**lease.view, "channel": "Mine", "password": "typed"}
        res = ms.save(tmp_path, screen, W, lease)
        assert not res.ok and res.code == "changed_without_lease" and res.now_unreadable
        res = ms.save(tmp_path, screen, W, lease, reset=True)
        assert res.ok, res
    disk = _disk(tmp_path)
    assert disk["channel"] == "Mine" and disk["broker"] == "mqtt.a.example"
    assert disk["password"] == "typed"
    assert sorted(tmp_path.glob(ms.FILENAME + ".unreadable.*"))


def test_v2_reset_from_the_screen_cannot_KEEP_a_password_it_never_held(tmp_path):
    _seed(tmp_path)
    with _lease(tmp_path) as lease:
        _file(tmp_path).write_text("")
        res = ms.save(tmp_path, dict(lease.view), W, lease, reset=True)
        assert not res.ok and res.code == "password_unknown"
    assert _file(tmp_path).read_text() == ""


def test_q15_save_over_a_file_unreadable_at_open_needs_an_explicit_reset(tmp_path):
    _plant(tmp_path, "{broken")
    with _lease(tmp_path) as lease:
        assert lease.loaded_state == "unreadable"
        intent = {**ms.defaults(), "channel": "Mine"}
        res = ms.save(tmp_path, intent, W, lease)
        assert not res.ok and res.code == "unreadable"
        assert _file(tmp_path).read_text() == "{broken"
        assert ms.save(tmp_path, intent, W, lease, reset=True).ok
    assert _disk(tmp_path)["channel"] == "Mine"
    assert [b.read_text() for b in tmp_path.glob(ms.FILENAME + ".unreadable.*")] == ["{broken"]


def test_q3_the_view_carries_KEEP_never_the_saved_password(tmp_path):
    _seed(tmp_path)
    with _lease(tmp_path) as lease:
        assert lease.view["password"] is ms.KEEP and lease.password_saved
        assert "s3cret" not in repr(vars(lease)) and "s3cret" not in repr(lease.view)
        assert ms.save(tmp_path, {**lease.view, "port": 1884}, W, lease).ok
    assert _disk(tmp_path)["password"] == "s3cret" and _disk(tmp_path)["port"] == 1884


def test_q3_KEEP_with_nothing_saved_saves_no_password(tmp_path):
    with _lease(tmp_path) as lease:
        assert lease.loaded_state == "absent" and not lease.password_saved
        assert ms.save(tmp_path, dict(lease.view), W, lease).ok
    assert _disk(tmp_path)["password"] == ""


def test_q3_KEEP_is_never_written_and_never_validates_as_a_value(tmp_path):
    assert ms.validate({**SAVED, "password": ms.KEEP})   # not a string
    _seed(tmp_path)
    assert '"password": "s3cret"' in _file(tmp_path).read_text()


@pytest.mark.parametrize("change", [{"broker": "mqtt.b.example"},
                                    {"username": "bob"}])
def test_q7_KEEP_across_an_account_change_refuses_before_writing(tmp_path, change):
    before = _seed(tmp_path)
    with _lease(tmp_path) as lease:
        res = ms.save(tmp_path, {**lease.view, **change}, W, lease)
        assert not res.ok and res.code == "password_owner_mismatch"
        assert "s3cret" not in (res.error or "")
        assert _disk(tmp_path) == before
        assert ms.save(tmp_path, {**lease.view, **change, "password": "new"},
                       W, lease).ok
    assert _disk(tmp_path)["password"] == "new"


@pytest.mark.parametrize("a,b", [("mqtt.a.example", " MQTT.A.Example. "),
                                 ("localhost", "127.0.0.1"), ("::1", "LOCALHOST")])
def test_q4_account_host_is_normalized_without_dns(a, b):
    assert ms.account({"broker": a, "username": "u"}) == \
        ms.account({"broker": b, "username": "u"})


def test_q8_a_blank_username_is_still_an_account(tmp_path):
    _seed(tmp_path, {**SAVED, "username": ""})
    with _lease(tmp_path) as lease:
        res = ms.save(tmp_path, {**lease.view, "broker": "mqtt.b.example"}, W, lease)
        assert res.code == "password_owner_mismatch"


def test_q19_saved_password_relation(tmp_path):
    with _lease(tmp_path) as lease:
        assert ms.saved_password_relation(lease, "x") == "none"
    _seed(tmp_path)
    with _lease(tmp_path) as lease:
        assert ms.saved_password_relation(lease, "s3cret") == "same"
        assert ms.saved_password_relation(lease, "") == "differs"
        assert ms.saved_password_relation(lease, "other") == "differs"


def test_v11_two_sessions_one_after_the_other_both_changes_land(tmp_path):
    _seed(tmp_path)
    first = _lease(tmp_path, user="alice")
    assert ms.acquire_lease(tmp_path, user="bob", tty="t").state == "busy"
    assert ms.save(tmp_path, {**first.view, "port": 1884}, W, first).ok
    first.release()
    with _lease(tmp_path, user="bob") as second:
        assert second.view["port"] == 1884               # sees the first's change
        assert ms.save(tmp_path, {**second.view, "channel": "Bobs"}, W, second).ok
    disk = _disk(tmp_path)
    assert disk["port"] == 1884 and disk["channel"] == "Bobs"
    assert disk["password"] == "s3cret"


def test_v7_a_second_save_in_the_same_lease_follows_its_own_write(tmp_path):
    _seed(tmp_path)
    with _lease(tmp_path) as lease:
        assert ms.save(tmp_path, {**lease.view, "port": 1884}, W, lease).ok
        assert ms.save(tmp_path, {**lease.view, "port": 1886}, W, lease).ok
    assert _disk(tmp_path)["port"] == 1886


def test_v10_a_home_with_no_config_dir_can_still_be_edited(tmp_path):
    """A fresh headless box may have no ~/.config: the lease (and so every
    edit) must not refuse there — save() always created the dir."""
    d = tmp_path / "home" / ".config" / "meshforge"
    (tmp_path / "home").mkdir()
    with _lease(d) as lease:
        assert ms.save(d, dict(SAVED), W, lease).ok
    assert _disk(d)["broker"] == "mqtt.a.example"
