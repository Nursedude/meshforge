"""F8 state model — `mqtt_settings.save_changes` (grill 2026-11-02).

Record: `.claude/plans/mqtt_f8_state_model_2026_11_02.md`. Every screen used
to save a full SNAPSHOT it loaded earlier, so a stale screen overwrote
another writer's save and the password travelled with whichever screen held
it. A screen now sends only what the operator CHANGED; the owner re-reads
the file at Save and applies them (Q2), owns the password (Q3/Q7), refuses a
field both sides changed (Q10), refuses over an unreadable file (Q15), and
excludes concurrent saves (Q9).

Every test passes its own tmp config dir.
"""
import fcntl
import json
import os
import sys

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from monitoring import mqtt_settings as ms  # noqa: E402
from utils.paths import atomic_write_text_preserving as W  # noqa: E402

A = {
    "broker": "mqtt.a.example", "port": 1883, "tls": "auto",
    "username": "alice", "password": "s3cret",
    "root_topic": "msh/HI/2/e", "channel": "Regional", "key": "AQ==",
    "auto_start": False, "auto_start_telemetry": True,
}


def _seed(d, intent=A):
    assert ms.save(d, dict(intent), W).ok


def _disk(d):
    return json.loads((d / ms.FILENAME).read_text())


def _base(d):
    """What a screen holds after opening: the intent WITHOUT the password."""
    return ms.editable(ms.load(d))


def _sc(d, view, changes, w=None, **kw):
    """save_changes as a SCREEN calls it: the view's intent AND the
    fingerprint of the password it opened with (Q17)."""
    return ms.save_changes(d, view.intent, changes, w or W,
                           base_password_fp=view.password_fp, **kw)


# --- Q3: the screen never holds the saved password -------------------------

def test_editable_drops_the_password_and_says_whether_one_is_saved(tmp_path):
    _seed(tmp_path)
    view = _base(tmp_path)
    assert "password" not in view.intent
    assert view.password_saved is True
    assert view.state == "ok"


def test_editable_of_absent_is_the_defaults_and_no_password(tmp_path):
    view = _base(tmp_path)
    assert view.state == "absent" and view.password_saved is False
    assert view.intent == {k: v for k, v in ms.defaults().items() if k != "password"}


# --- Q2: the stale-writer test (the one every round would have failed) -----

def test_two_screens_each_change_one_field_and_both_land(tmp_path):
    _seed(tmp_path)
    screen_a, screen_b = _base(tmp_path), _base(tmp_path)
    assert ms.save_changes(tmp_path, screen_a.intent, {"tls": True}, W).ok
    assert ms.save_changes(tmp_path, screen_b.intent, {"port": 1884}, W).ok
    disk = _disk(tmp_path)
    assert disk["tls"] is True and disk["port"] == 1884
    assert disk["password"] == "s3cret"


def test_an_empty_change_set_rewrites_nothing_the_operator_chose(tmp_path):
    _seed(tmp_path)
    before = _disk(tmp_path)
    assert ms.save_changes(tmp_path, _base(tmp_path).intent, {}, W).ok
    assert _disk(tmp_path) == before


# --- Q3/Q7/Q8: the password belongs to an account --------------------------

def test_same_account_keeps_the_saved_password_when_absent_from_changes(tmp_path):
    _seed(tmp_path)
    assert ms.save_changes(tmp_path, _base(tmp_path).intent, {"channel": "LongFast"}, W).ok
    assert _disk(tmp_path)["password"] == "s3cret"


def test_an_explicit_empty_password_clears_it(tmp_path):
    _seed(tmp_path)
    assert _sc(tmp_path, _base(tmp_path), {"password": ""}).ok
    assert _disk(tmp_path)["password"] == ""


def test_a_new_password_is_written(tmp_path):
    _seed(tmp_path)
    assert _sc(tmp_path, _base(tmp_path), {"password": "n3w"}).ok
    assert _disk(tmp_path)["password"] == "n3w"


@pytest.mark.parametrize("change", [
    {"broker": "mqtt.b.example"},
    {"username": "bob"},
    {"broker": "mqtt.b.example", "username": "bob"},
])
def test_Q7_changing_the_account_with_a_kept_password_refuses_and_writes_nothing(tmp_path, change):
    _seed(tmp_path)
    before = (tmp_path / ms.FILENAME).read_bytes()
    res = ms.save_changes(tmp_path, _base(tmp_path).intent, change, W)
    assert not res.ok and res.code == "password_owner_mismatch"
    assert "alice@mqtt.a.example" in res.error
    assert (tmp_path / ms.FILENAME).read_bytes() == before


def test_Q7_changing_the_account_with_an_answer_saves(tmp_path):
    _seed(tmp_path)
    res = _sc(tmp_path, _base(tmp_path),
                          {"broker": "mqtt.b.example", "password": ""})
    assert res.ok and _disk(tmp_path)["password"] == ""


def test_changing_the_account_with_no_saved_password_needs_no_answer(tmp_path):
    _seed(tmp_path, {**A, "password": ""})
    assert ms.save_changes(tmp_path, _base(tmp_path).intent,
                           {"broker": "mqtt.b.example"}, W).ok


@pytest.mark.parametrize("old,new", [
    ("localhost", "127.0.0.1"), ("127.0.0.1", "::1"),
    ("MQTT.A.example", "mqtt.a.example."), (" mqtt.a.example", "mqtt.a.example"),
])
def test_Q4_host_spellings_of_one_broker_are_one_account(old, new):
    assert ms.account({"broker": old, "username": "u"}) == \
        ms.account({"broker": new, "username": "u"})


def test_Q4_a_short_name_and_its_fqdn_are_different_accounts():
    assert ms.account({"broker": "mqtt", "username": "u"}) != \
        ms.account({"broker": "mqtt.lan", "username": "u"})


def test_Q8_a_blank_username_is_still_an_account(tmp_path):
    _seed(tmp_path, {**A, "username": ""})
    res = ms.save_changes(tmp_path, _base(tmp_path).intent, {"broker": "mqtt.b.example"}, W)
    assert not res.ok and res.code == "password_owner_mismatch"
    res = ms.save_changes(tmp_path, _base(tmp_path).intent, {"port": 1884}, W)
    assert res.ok and _disk(tmp_path)["password"] == "s3cret"


# --- Q10: a field both sides changed is refused, per field -----------------

def test_Q10_a_field_changed_on_disk_and_by_me_is_refused_with_both_values(tmp_path):
    _seed(tmp_path)
    stale = _base(tmp_path)
    assert ms.save_changes(tmp_path, _base(tmp_path).intent, {"tls": False}, W).ok
    before = (tmp_path / ms.FILENAME).read_bytes()
    res = ms.save_changes(tmp_path, stale.intent, {"tls": True, "port": 1884}, W)
    assert not res.ok and res.code == "conflict"
    assert res.conflicts == {"tls": (False, True)}
    assert (tmp_path / ms.FILENAME).read_bytes() == before   # all or nothing


def test_Q10_both_sides_making_the_same_change_is_no_conflict(tmp_path):
    _seed(tmp_path)
    stale = _base(tmp_path)
    assert ms.save_changes(tmp_path, _base(tmp_path).intent, {"tls": True}, W).ok
    assert ms.save_changes(tmp_path, stale.intent, {"tls": True}, W).ok


def test_Q10_a_one_sided_disk_change_survives_my_unrelated_save(tmp_path):
    _seed(tmp_path)
    stale = _base(tmp_path)
    assert ms.save_changes(tmp_path, _base(tmp_path).intent, {"channel": "Other"}, W).ok
    assert ms.save_changes(tmp_path, stale.intent, {"port": 1884}, W).ok
    assert _disk(tmp_path)["channel"] == "Other"


# --- validation still at the boundary --------------------------------------

def test_an_invalid_change_is_refused_naming_the_field(tmp_path):
    _seed(tmp_path)
    res = ms.save_changes(tmp_path, _base(tmp_path).intent, {"port": 0}, W)
    assert not res.ok and res.code == "invalid" and any("port" in e for e in res.errors)


def test_an_unknown_key_in_changes_is_refused(tmp_path):
    _seed(tmp_path)
    res = ms.save_changes(tmp_path, _base(tmp_path).intent, {"topic": "x"}, W)
    assert not res.ok and res.code == "invalid"


# --- Q15: absent / legacy / unreadable --------------------------------------

def test_Q15_absent_applies_the_changes_over_the_defaults(tmp_path):
    res = ms.save_changes(tmp_path, _base(tmp_path).intent, {"channel": "Regional"}, W)
    assert res.ok
    assert _disk(tmp_path)["broker"] == ms.defaults()["broker"]
    assert _disk(tmp_path)["channel"] == "Regional"


def test_Q15_unreadable_refuses_and_keeps_the_bytes(tmp_path):
    (tmp_path / ms.FILENAME).write_text("{not json")
    res = ms.save_changes(tmp_path, ms.editable(ms.load(tmp_path)).intent,
                          {"channel": "Regional"}, W)
    assert not res.ok and res.code == "unreadable" and "Reset" in res.error
    assert (tmp_path / ms.FILENAME).read_text() == "{not json"


def test_Q15_legacy_applies_over_the_translation_and_keeps_pre_v1(tmp_path):
    legacy = {"broker": "mqtt.a.example", "port": 1883, "topic": "msh/HI/2/e/Regional/#",
              "username": "alice", "password": "s3cret"}
    (tmp_path / ms.FILENAME).write_text(json.dumps(legacy))
    assert ms.save_changes(tmp_path, _base(tmp_path).intent, {"port": 1884}, W).ok
    disk = _disk(tmp_path)
    assert disk["port"] == 1884 and disk["password"] == "s3cret"
    assert disk["channel"] == "Regional"
    assert (tmp_path / (ms.FILENAME + ms.PRE_V1_SUFFIX)).exists()


# --- Q9: concurrent saves are excluded --------------------------------------

def test_Q9_a_held_lock_refuses_after_the_timeout_and_writes_nothing(tmp_path):
    d = tmp_path / "cfg"
    _seed(d)
    before = (d / ms.FILENAME).read_bytes()
    fd = os.open(ms.lock_target(d), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        res = ms.save_changes(d, _base(d).intent, {"port": 1884}, W,
                              lock_timeout=0.2)
    finally:
        os.close(fd)
    assert not res.ok and res.code == "busy"
    assert (d / ms.FILENAME).read_bytes() == before


def test_Q9_the_lock_is_released_after_a_save(tmp_path):
    _seed(tmp_path)
    assert ms.save_changes(tmp_path, _base(tmp_path).intent, {"port": 1884}, W).ok
    fd = os.open(ms.lock_target(tmp_path), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)   # raises if still held
    finally:
        os.close(fd)


def test_Q9_the_lock_is_released_after_a_refusal(tmp_path):
    _seed(tmp_path)
    ms.save_changes(tmp_path, _base(tmp_path).intent, {"port": 0}, W)
    fd = os.open(ms.lock_target(tmp_path), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


def test_Q16_the_full_write_takes_the_same_lock(tmp_path):
    d = tmp_path / "cfg"
    fd = os.open(ms.lock_target(d), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        res = ms.save(d, dict(A), W, lock_timeout=0.2)
    finally:
        os.close(fd)
    assert not res.ok and res.code == "busy"
    assert not d.exists()


def test_a_save_result_says_what_changed(tmp_path):
    """The TUI reports what was WRITTEN, never what it meant to write."""
    _seed(tmp_path)
    res = ms.save_changes(tmp_path, _base(tmp_path).intent, {"port": 1884}, W)
    assert res.ok and res.intent["port"] == 1884 and "password" not in res.intent


# --- review r1 (contextless non-author, 2026-11-02) ------------------------

def test_r1_2_a_supplied_password_is_refused_for_an_account_it_was_not_entered_for(tmp_path):
    """The screen typed a password for alice@A (A unchanged, so not in
    changes); another writer saved broker B meanwhile. The merge would write
    A's password against B — refuse (the owner rule holds for a SUPPLIED
    password too, not only a kept one)."""
    _seed(tmp_path)
    stale = _base(tmp_path)
    assert _sc(tmp_path, _base(tmp_path),
                           {"broker": "mqtt.b.example", "password": ""}).ok
    before = (tmp_path / ms.FILENAME).read_bytes()
    res = _sc(tmp_path, stale, {"password": "for-A"})
    # the saved password changed too (cleared): Q17 asks about that FIRST
    assert not res.ok and res.code == "conflict" and "password" in res.conflicts
    stale.password_fp = res.password_fp        # "use mine"
    res = _sc(tmp_path, stale, {"password": "for-A"})
    assert not res.ok and res.code == "password_owner_mismatch"
    assert (tmp_path / ms.FILENAME).read_bytes() == before


def test_r1_2_a_supplied_password_for_the_written_account_saves(tmp_path):
    _seed(tmp_path)
    stale = _base(tmp_path)
    assert _sc(tmp_path, _base(tmp_path), {"channel": "Other"}).ok
    assert _sc(tmp_path, stale, {"password": "n3w"}).ok
    assert _disk(tmp_path)["password"] == "n3w"


def test_r1_4_a_lock_error_is_a_result_not_a_raise(tmp_path):
    parent = tmp_path / "p"                    # the lock target (r3 #4)
    parent.mkdir()
    d = parent / "cfg"
    os.chmod(parent, 0o000)
    try:
        if os.geteuid() == 0:
            pytest.skip("root reads a 000 dir")
        res = ms.save_changes(d, ms.editable(ms.Loaded("absent")).intent, {"port": 1884}, W)
        assert not res.ok and res.code == "write"
        res = ms.save(d, dict(A), W)
        assert not res.ok and res.code == "write"
    finally:
        os.chmod(parent, 0o700)


def test_r1_5_a_refused_save_creates_no_config_dir(tmp_path):
    d = tmp_path / "never"
    res = ms.save_changes(d, ms.editable(ms.Loaded("absent")).intent, {"port": 0}, W)
    assert not res.ok and res.code == "invalid"
    assert not d.exists()


def test_r1_9_the_screen_helpers_are_public():
    for name in ("same", "account_label", "public_view"):
        assert callable(getattr(ms, name)), name


# --- review r2: the password travels WITH the account it was typed for ------

def test_r2_1_a_refusal_names_the_account_being_written_and_its_answer_saves(tmp_path):
    _seed(tmp_path)
    stale = _base(tmp_path)
    assert _sc(tmp_path, _base(tmp_path),
                           {"username": "xavier", "password": ""}).ok
    typed = ms.Secret("for-alice", ms.account(stale.intent))
    res = _sc(tmp_path, stale, {"password": typed})
    assert not res.ok and res.code == "conflict"     # Q17: cleared elsewhere
    stale.password_fp = res.password_fp
    res = _sc(tmp_path, stale, {"password": typed})
    assert not res.ok and res.code == "password_owner_mismatch"
    assert res.account == ("mqtt.a.example", "xavier")
    res = _sc(tmp_path, stale,
                          {"password": ms.Secret("for-x", res.account)})
    assert res.ok and _disk(tmp_path)["password"] == "for-x"


def test_r2_2_a_secret_typed_for_B_is_never_written_for_C(tmp_path):
    _seed(tmp_path)
    b_acct = ("mqtt.b.example", "alice")
    assert _sc(tmp_path, _base(tmp_path),
               {"broker": "mqtt.c.example", "password": ""}).ok
    # the screen "kept the saved value" for broker: base moved to C, broker
    # dropped — but the secret still names B
    res = _sc(tmp_path, _base(tmp_path), {"password": ms.Secret("pwB", b_acct)})
    assert not res.ok and res.code == "password_owner_mismatch"
    assert _disk(tmp_path)["password"] == ""


def test_r2_7_a_first_ever_save_is_locked_too(tmp_path):
    d = tmp_path / "cfg"                       # does not exist yet
    fd = os.open(tmp_path, os.O_RDONLY)        # its nearest existing ancestor
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        res = ms.save_changes(d, ms.editable(ms.Loaded("absent")).intent,
                              {"port": 1884}, W, lock_timeout=0.2)
    finally:
        os.close(fd)
    assert not res.ok and res.code == "busy" and not d.exists()


def test_r2_a_secret_value_is_what_is_written_never_its_repr(tmp_path):
    _seed(tmp_path)
    acct = ms.account(A)
    assert _sc(tmp_path, _base(tmp_path),
                           {"password": ms.Secret("v4lue", acct)}).ok
    assert _disk(tmp_path)["password"] == "v4lue"
    assert "v4lue" not in repr(ms.Secret("v4lue", acct))


# --- review r3 deploy blockers (grill record, next-session list) -----------

def _hold(path):
    fd = os.open(path, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def test_r3_4_the_lock_target_is_the_config_dirs_parent(tmp_path):
    assert ms.lock_target(tmp_path / "cfg") == tmp_path


def test_r3_4_the_lock_target_does_not_move_after_the_first_save(tmp_path):
    """Writer 1's first-ever save mkdirs the config dir; writer 2 must still
    meet writer 1's lock (r3 #4: it used to lock the NEW dir instead)."""
    d = tmp_path / "cfg"
    assert ms.save_changes(d, _base(d).intent, {"port": 1884}, W).ok   # creates d
    fd = _hold(tmp_path)                       # writer 1, still mid-save
    try:
        res = ms.save_changes(d, _base(d).intent, {"port": 1885}, W, lock_timeout=0.2)
    finally:
        os.close(fd)
    assert not res.ok and res.code == "busy"
    assert _disk(d)["port"] == 1884


def test_r3_4_a_missing_parent_refuses_and_creates_nothing(tmp_path):
    """Creating the parent would move the lock target for the next writer
    (and under sudo leave a root-owned ~/.config) — refuse, say what to do."""
    d = tmp_path / "absent" / "cfg"
    res = ms.save_changes(d, ms.editable(ms.Loaded("absent")).intent, {"port": 1884}, W)
    assert not res.ok and res.code == "write"
    assert str(tmp_path / "absent") in res.error and "create" in res.error
    assert not (tmp_path / "absent").exists()
    res = ms.save(d, dict(A), W)
    assert not res.ok and res.code == "write" and not (tmp_path / "absent").exists()


def _unreadable(d, text="{not json"):
    d.mkdir(exist_ok=True)
    (d / ms.FILENAME).write_text(text)


def test_r3_1_reset_over_a_file_fixed_meanwhile_does_not_overwrite_it(tmp_path):
    """The screen saw `unreadable`; the operator fixed the file before
    picking Reset. Reset re-checks under the lock: the file is readable, so
    the changes go through the normal both-sides check — nothing is lost."""
    d = tmp_path / "cfg"
    _unreadable(d)
    screen = _base(d)                          # defaults (unreadable)
    (d / ms.FILENAME).unlink()                 # the operator fixed it meanwhile:
    _seed(d)                                   # A
    res = ms.save_changes(d, screen.intent, {"channel": "Mine"}, W, reset=True)
    assert not res.ok and res.code == "conflict" and "channel" in res.conflicts
    assert _disk(d)["broker"] == A["broker"] and _disk(d)["password"] == "s3cret"
    assert not list(d.glob(ms.FILENAME + ".unreadable.*"))


def test_r3_1_reset_merges_an_unconflicted_change_over_the_fixed_file(tmp_path):
    d = tmp_path / "cfg"
    _unreadable(d)
    screen = _base(d)
    (d / ms.FILENAME).unlink()
    _seed(d)
    assert ms.save_changes(d, screen.intent, {"auto_start": True}, W, reset=True).ok
    disk = _disk(d)
    assert disk["auto_start"] is True and disk["password"] == "s3cret"
    assert disk["broker"] == A["broker"]


def test_r3_1_reset_takes_the_lock(tmp_path):
    d = tmp_path / "cfg"
    _unreadable(d)
    fd = _hold(tmp_path)
    try:
        res = ms.save_changes(d, _base(d).intent, {"channel": "X"}, W,
                              lock_timeout=0.2, reset=True)
    finally:
        os.close(fd)
    assert not res.ok and res.code == "busy"
    assert (d / ms.FILENAME).read_text() == "{not json"


def test_r3_1_reset_rebuilds_from_defaults_and_keeps_the_bytes(tmp_path):
    d = tmp_path / "cfg"
    _unreadable(d)
    res = ms.save_changes(d, _base(d).intent, {"channel": "Regional"}, W, reset=True)
    assert res.ok
    disk = _disk(d)
    assert disk["channel"] == "Regional" and disk["broker"] == ms.defaults()["broker"]
    assert (d / f"{ms.FILENAME}.unreadable.1").read_text() == "{not json"


def test_r3_2_reset_refuses_a_secret_typed_for_another_account(tmp_path):
    d = tmp_path / "cfg"
    _unreadable(d)
    typed = ms.Secret("pwB", ("mqtt.b.example", "bob"))
    res = _sc(d, _base(d),
                          {"broker": "mqtt.c.example", "username": "bob",
                           "password": typed}, reset=True)
    assert not res.ok and res.code == "password_owner_mismatch"
    assert res.account == ("mqtt.c.example", "bob")
    assert (d / ms.FILENAME).read_text() == "{not json"


def test_r3_2_reset_writes_a_secret_for_the_account_it_names(tmp_path):
    d = tmp_path / "cfg"
    _unreadable(d)
    acct = ("mqtt.c.example", "bob")
    assert _sc(d, _base(d),
                           {"broker": "mqtt.c.example", "username": "bob",
                            "password": ms.Secret("pwC", acct)}, reset=True).ok
    assert _disk(d)["password"] == "pwC"


# --- Q17: the password joins the both-sides check, by FINGERPRINT ----------

PLAINTEXTS = ("s3cret", "pwA", "pwB")


def _no_plaintext(res):
    blob = repr(res) + (res.error or "") + repr(res.conflicts)
    for p in PLAINTEXTS:
        assert p not in blob, f"plaintext {p!r} leaked into the result"


def test_Q17_editable_carries_a_fingerprint_not_the_password(tmp_path):
    _seed(tmp_path)
    view = _base(tmp_path)
    assert view.password_fp and "s3cret" not in view.password_fp
    assert view.password_fp == ms.password_fingerprint("s3cret")
    assert ms.password_fingerprint("s3cret") != ms.password_fingerprint("other")
    assert _base(tmp_path / "none").password_fp == ms.password_fingerprint("")


def test_Q17_a_password_changed_on_disk_and_by_me_is_a_conflict(tmp_path):
    _seed(tmp_path)
    a = _base(tmp_path)
    b = _base(tmp_path)
    assert ms.save_changes(tmp_path, b.intent, {"password": "pwB"}, W,
                           base_password_fp=b.password_fp).ok
    before = (tmp_path / ms.FILENAME).read_bytes()
    res = ms.save_changes(tmp_path, a.intent, {"password": "pwA"}, W,
                          base_password_fp=a.password_fp)
    assert not res.ok and res.code == "conflict" and "password" in res.conflicts
    assert res.password_fp == ms.password_fingerprint("pwB")
    _no_plaintext(res)
    assert (tmp_path / ms.FILENAME).read_bytes() == before


def test_Q17_answering_with_the_current_fingerprint_writes_mine(tmp_path):
    _seed(tmp_path)
    a = _base(tmp_path)
    b = _base(tmp_path)
    assert ms.save_changes(tmp_path, b.intent, {"password": "pwB"}, W,
                           base_password_fp=b.password_fp).ok
    res = ms.save_changes(tmp_path, a.intent, {"password": "pwA"}, W,
                          base_password_fp=a.password_fp)
    assert ms.save_changes(tmp_path, a.intent, {"password": "pwA"}, W,
                           base_password_fp=res.password_fp).ok
    assert _disk(tmp_path)["password"] == "pwA"


def test_Q17_clearing_over_a_password_changed_on_disk_is_a_conflict(tmp_path):
    _seed(tmp_path)
    a = _base(tmp_path)
    b = _base(tmp_path)
    assert ms.save_changes(tmp_path, b.intent, {"password": "pwB"}, W,
                           base_password_fp=b.password_fp).ok
    res = ms.save_changes(tmp_path, a.intent, {"password": ""}, W,
                          base_password_fp=a.password_fp)
    assert not res.ok and res.code == "conflict" and "password" in res.conflicts
    assert _disk(tmp_path)["password"] == "pwB"


def test_Q17_both_sides_setting_the_same_password_is_no_conflict(tmp_path):
    _seed(tmp_path)
    a = _base(tmp_path)
    b = _base(tmp_path)
    assert ms.save_changes(tmp_path, b.intent, {"password": "pwB"}, W,
                           base_password_fp=b.password_fp).ok
    assert ms.save_changes(tmp_path, a.intent, {"password": "pwB"}, W,
                           base_password_fp=a.password_fp).ok


def test_Q17_a_disk_password_change_survives_my_unrelated_save(tmp_path):
    _seed(tmp_path)
    a = _base(tmp_path)
    b = _base(tmp_path)
    assert ms.save_changes(tmp_path, b.intent, {"password": "pwB"}, W,
                           base_password_fp=b.password_fp).ok
    assert ms.save_changes(tmp_path, a.intent, {"port": 1884}, W).ok
    assert _disk(tmp_path)["password"] == "pwB"


def test_Q17_a_password_sent_without_its_fingerprint_is_refused(tmp_path):
    """A screen that sends a password but not WHICH saved password it opened
    with cannot be checked — refuse loudly, never skip the check (hfm #3)."""
    _seed(tmp_path)
    before = (tmp_path / ms.FILENAME).read_bytes()
    res = ms.save_changes(tmp_path, _base(tmp_path).intent, {"password": "pwA"}, W)
    assert not res.ok and res.code == "invalid" and "base_password_fp" in res.error
    assert (tmp_path / ms.FILENAME).read_bytes() == before
